"""Native forward-scene sensing for SMB1 live planning.

The radar reads one coherent 2 KiB internal-RAM snapshot from Mesen and decodes
what the game already knows about the near field. This is intentionally not
screen scraping: enemy-object slots, the active power-up object, player
capability state, and the game's collision block buffers are the primary sensor
surface.

The two SMB1 block buffers form a rolling 32-column collision map. A world X
column maps through ``(world_x >> 4) & 0x1f``; columns 0..15 live at $0500 and
16..31 at $05d0. Each column stores 13 collision rows spaced by $10 bytes.
Only collision-relevant metatiles are retained by the original game, so zero is
an empty/non-colliding cell for this near-field occupancy use.

SMB1 stores the active power-up in enemy-object slot 5 with Enemy_ID=$2e. The
radar classifies that object into a reward channel instead of treating it as a
hostile enemy hazard.
"""

from __future__ import annotations

from dataclasses import dataclass

from fami_pixel.adapters.mesen import MesenCore, read_nes_internal_ram


ENEMY_SLOTS = 6
POWER_UP_SLOT = 5
POWER_UP_OBJECT_ID = 0x2E
ADDR_POWER_UP_TYPE = 0x0039
ADDR_ENEMY_FLAG = 0x000F
ADDR_ENEMY_ID = 0x0016
ADDR_ENEMY_STATE = 0x001E
ADDR_ENEMY_PAGE = 0x006E
ADDR_ENEMY_X = 0x0087
ADDR_ENEMY_Y_HIGH = 0x00B6
ADDR_ENEMY_Y = 0x00CF
ADDR_SCREEN_RIGHT_PAGE = 0x071B
ADDR_SCREEN_RIGHT_X = 0x071D
ADDR_PLAYER_STATUS = 0x0756
ADDR_STAR_INVINCIBLE_TIMER = 0x079F
BLOCK_BUFFER_1 = 0x0500
BLOCK_BUFFER_2 = 0x05D0
BLOCK_ROWS = 13

POWER_UP_NAMES = {
    0: "mushroom",
    1: "fire_flower",
    2: "star",
    3: "one_up",
}

# Rows 8..12 cover the lower playfield where normal ground, pipes, and pits live.
LOWER_PLAYFIELD_FIRST_ROW = 8
LOWER_PLAYFIELD_LAST_ROW = 12


@dataclass(frozen=True)
class RadarEnemy:
    slot: int
    enemy_id: int
    state: int
    world_x: int
    y: int
    dx: int


@dataclass(frozen=True)
class RadarReward:
    slot: int
    reward_type: str
    power_up_type: int
    state: int
    world_x: int
    y: int
    dx: int


@dataclass(frozen=True)
class RadarColumn:
    world_x: int
    dx: int
    surface_row: int | None
    surface_y: int | None

    @property
    def has_ground(self) -> bool:
        return self.surface_row is not None


@dataclass(frozen=True)
class Smb1RadarSnapshot:
    player_x: int
    lookahead_px: int
    screen_right_x: int
    enemies: tuple[RadarEnemy, ...]
    rewards: tuple[RadarReward, ...]
    columns: tuple[RadarColumn, ...]
    nearest_enemy_dx: int | None
    nearest_gap_dx: int | None
    nearest_obstacle_dx: int | None
    nearest_reward_dx: int | None
    nearest_reward_type: str | None
    reference_surface_row: int | None
    player_status: int
    star_invincible_timer: int

    @property
    def hazard_ahead(self) -> bool:
        return any(
            value is not None and value <= 80
            for value in (self.nearest_enemy_dx, self.nearest_gap_dx, self.nearest_obstacle_dx)
        )

    @property
    def invincible(self) -> bool:
        return self.star_invincible_timer > 0

    def to_payload(self) -> dict:
        return {
            "player_x": self.player_x,
            "lookahead_px": self.lookahead_px,
            "screen_right_x": self.screen_right_x,
            "nearest_enemy_dx": self.nearest_enemy_dx,
            "nearest_gap_dx": self.nearest_gap_dx,
            "nearest_obstacle_dx": self.nearest_obstacle_dx,
            "nearest_reward_dx": self.nearest_reward_dx,
            "nearest_reward_type": self.nearest_reward_type,
            "reference_surface_row": self.reference_surface_row,
            "hazard_ahead": self.hazard_ahead,
            "player_status": self.player_status,
            "star_invincible_timer": self.star_invincible_timer,
            "invincible": self.invincible,
            "enemies": [
                {
                    "slot": enemy.slot,
                    "id": enemy.enemy_id,
                    "state": enemy.state,
                    "x": enemy.world_x,
                    "y": enemy.y,
                    "dx": enemy.dx,
                }
                for enemy in self.enemies
            ],
            "rewards": [
                {
                    "slot": reward.slot,
                    "type": reward.reward_type,
                    "power_up_type": reward.power_up_type,
                    "state": reward.state,
                    "x": reward.world_x,
                    "y": reward.y,
                    "dx": reward.dx,
                }
                for reward in self.rewards
            ],
            "columns": [
                {
                    "x": column.world_x,
                    "dx": column.dx,
                    "surface_row": column.surface_row,
                    "surface_y": column.surface_y,
                }
                for column in self.columns
            ],
        }


def _ram_byte(ram: bytes, address: int) -> int:
    if not 0 <= address < len(ram):
        raise ValueError(f"RAM snapshot does not contain address 0x{address:04X}")
    return int(ram[address])


def _block_address(world_x: int, row: int) -> int:
    if not 0 <= row < BLOCK_ROWS:
        raise ValueError("block row must be in range 0..12")
    column = (int(world_x) >> 4) & 0x1F
    base = BLOCK_BUFFER_1 if column < 16 else BLOCK_BUFFER_2
    return base + (column & 0x0F) + (row << 4)


def _surface_row(ram: bytes, world_x: int) -> int | None:
    for row in range(LOWER_PLAYFIELD_FIRST_ROW, LOWER_PLAYFIELD_LAST_ROW + 1):
        if _ram_byte(ram, _block_address(world_x, row)) != 0:
            return row
    return None


def decode_smb1_radar(
    ram: bytes,
    *,
    player_x: int,
    lookahead_px: int = 192,
    sample_step_px: int = 16,
) -> Smb1RadarSnapshot:
    """Decode hostile enemies, active rewards, capability, and near-field terrain."""
    if lookahead_px <= 0:
        raise ValueError("lookahead_px must be > 0")
    if sample_step_px <= 0:
        raise ValueError("sample_step_px must be > 0")

    screen_right_x = (
        (_ram_byte(ram, ADDR_SCREEN_RIGHT_PAGE) << 8)
        | _ram_byte(ram, ADDR_SCREEN_RIGHT_X)
    )
    # Stay inside the game's currently prepared near-field map. One extra tile
    # is safe/useful because the block-buffer loader works ahead of the viewport.
    available_ahead = max(sample_step_px, screen_right_x - int(player_x) + sample_step_px)
    effective_lookahead = min(int(lookahead_px), available_ahead)

    power_up_type = _ram_byte(ram, ADDR_POWER_UP_TYPE)
    player_status = _ram_byte(ram, ADDR_PLAYER_STATUS)
    star_invincible_timer = _ram_byte(ram, ADDR_STAR_INVINCIBLE_TIMER)

    enemies: list[RadarEnemy] = []
    rewards: list[RadarReward] = []
    for slot in range(ENEMY_SLOTS):
        flag = _ram_byte(ram, ADDR_ENEMY_FLAG + slot)
        if flag == 0:
            continue
        if _ram_byte(ram, ADDR_ENEMY_Y_HIGH + slot) != 1:
            continue
        state = _ram_byte(ram, ADDR_ENEMY_STATE + slot)
        enemy_id = _ram_byte(ram, ADDR_ENEMY_ID + slot)
        world_x = (
            (_ram_byte(ram, ADDR_ENEMY_PAGE + slot) << 8)
            | _ram_byte(ram, ADDR_ENEMY_X + slot)
        )
        dx = world_x - int(player_x)
        if dx < -16 or dx > effective_lookahead:
            continue

        if enemy_id == POWER_UP_OBJECT_ID:
            rewards.append(
                RadarReward(
                    slot=slot,
                    reward_type=POWER_UP_NAMES.get(power_up_type, f"power_up_{power_up_type}"),
                    power_up_type=power_up_type,
                    state=state,
                    world_x=world_x,
                    y=_ram_byte(ram, ADDR_ENEMY_Y + slot),
                    dx=dx,
                )
            )
            continue

        # Bit $20 is used by normal enemy death/defeat states; do not treat a
        # defeated object as an approaching collision hazard.
        if state & 0x20:
            continue
        enemies.append(
            RadarEnemy(
                slot=slot,
                enemy_id=enemy_id,
                state=state,
                world_x=world_x,
                y=_ram_byte(ram, ADDR_ENEMY_Y + slot),
                dx=dx,
            )
        )
    enemies.sort(key=lambda enemy: (enemy.dx, enemy.slot))
    rewards.sort(key=lambda reward: (reward.dx, reward.slot))

    # Align terrain samples to 16-pixel metatile columns, starting with the next
    # column ahead of Mario rather than repeatedly probing his current cell.
    start_x = ((int(player_x) >> 4) + 1) << 4
    end_x = int(player_x) + effective_lookahead
    columns: list[RadarColumn] = []
    for world_x in range(start_x, end_x + 1, sample_step_px):
        row = _surface_row(ram, world_x)
        columns.append(
            RadarColumn(
                world_x=world_x,
                dx=world_x - int(player_x),
                surface_row=row,
                surface_y=None if row is None else 32 + row * 16,
            )
        )

    reference_surface_row = _surface_row(ram, int(player_x))
    nearest_gap_dx = next((column.dx for column in columns if not column.has_ground), None)
    nearest_obstacle_dx = None
    if reference_surface_row is not None:
        nearest_obstacle_dx = next(
            (
                column.dx
                for column in columns
                if column.surface_row is not None
                and column.surface_row < reference_surface_row
            ),
            None,
        )

    nearest_enemy_dx = next((enemy.dx for enemy in enemies if enemy.dx >= 0), None)
    nearest_reward = next((reward for reward in rewards if reward.dx >= 0), None)
    return Smb1RadarSnapshot(
        player_x=int(player_x),
        lookahead_px=effective_lookahead,
        screen_right_x=screen_right_x,
        enemies=tuple(enemies),
        rewards=tuple(rewards),
        columns=tuple(columns),
        nearest_enemy_dx=nearest_enemy_dx,
        nearest_gap_dx=nearest_gap_dx,
        nearest_obstacle_dx=nearest_obstacle_dx,
        nearest_reward_dx=None if nearest_reward is None else nearest_reward.dx,
        nearest_reward_type=None if nearest_reward is None else nearest_reward.reward_type,
        reference_surface_row=reference_surface_row,
        player_status=player_status,
        star_invincible_timer=star_invincible_timer,
    )


def read_smb1_radar(
    core: MesenCore,
    *,
    player_x: int,
    lookahead_px: int = 192,
) -> Smb1RadarSnapshot:
    """Read one coherent native RAM snapshot and decode the SMB1 radar."""
    return decode_smb1_radar(
        read_nes_internal_ram(core),
        player_x=player_x,
        lookahead_px=lookahead_px,
    )
