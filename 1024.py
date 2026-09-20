"""
NEBULA PLANET — a single-file mashup
  * Don't Starve  -> survival: health, hunger, day/night, night hounds, campfires
  * Terraria      -> 2D sandbox: mine, build, explore, craft
  * "2.5D"        -> every tile is drawn as a little extruded cube
                     (front face + top face + right face), entities cast
                     shadows, and a dynamic light map handles night.

Requirements: pygame  (pip install pygame)
Run:          python nebula_planet.py

Controls
  A / D  or  <- / ->   move
  W / SPACE / UP       jump
  LEFT CLICK           mine the tile under the cursor (hold to keep mining)
                       -- or attack, if an enemy is under the cursor
  RIGHT CLICK          place the selected block
  1..6                 select hotbar slot
  E                    eat a berry
  C                    place a campfire (costs 3 wood)
  R                    restart (when dead)
  ESC                  quit
"""

import pygame
import random
import math
import sys
from collections import defaultdict

# ----------------------------------------------------------------------------
# SETUP
# ----------------------------------------------------------------------------
pygame.init()
SCREEN_W, SCREEN_H = 1024, 640
SCREEN = pygame.display.set_mode((SCREEN_W, SCREEN_H))
pygame.display.set_caption("Nebula Planet  -  Don't Starve x Terraria (2.5D)")
CLOCK = pygame.time.Clock()

FONT   = pygame.font.SysFont("consolas,couriernew,monospace", 15)
FONT_B = pygame.font.SysFont("consolas,couriernew,monospace", 15, bold=True)
FONT_S = pygame.font.SysFont("consolas,couriernew,monospace", 12)
BIG    = pygame.font.SysFont("consolas,couriernew,monospace", 46, bold=True)

# ----------------------------------------------------------------------------
# CONSTANTS
# ----------------------------------------------------------------------------
TILE   = 24            # tile size in pixels
DEPTH  = 6             # 2.5D extrusion depth (how far the "cube" recedes)
WORLD_W, WORLD_H = 420, 140

FIXED_DT  = 1.0 / 60.0
GRAVITY   = 1500.0
MAX_FALL  = 880.0
MOVE_SPEED = 178.0
JUMP_V     = -515.0
DAY_LEN    = 100.0     # seconds for one full day/night cycle

# tile ids
AIR, DIRT, GRASS, STONE, SAND, WOOD, LEAF, COAL, IRON, PLANK, BUSH = range(11)
ITEM_BERRY = 100

SOLID = {DIRT, GRASS, STONE, SAND, WOOD, COAL, IRON, PLANK}

TILE_COLORS = {
    DIRT:  (118, 78, 48),
    GRASS: (88, 148, 58),
    STONE: (110, 110, 120),
    SAND:  (208, 188, 118),
    WOOD:  (118, 82, 46),
    LEAF:  (62, 130, 56),
    COAL:  (66, 66, 72),
    IRON:  (158, 138, 124),
    PLANK: (164, 122, 72),
    BUSH:  (52, 114, 52),
}

HARDNESS = {
    DIRT: 0.14, GRASS: 0.14, STONE: 0.32, SAND: 0.12, WOOD: 0.28,
    COAL: 0.42, IRON: 0.58, PLANK: 0.18, BUSH: 0.06, LEAF: 0.06,
}

DROPS = {
    DIRT: DIRT, GRASS: GRASS, STONE: STONE, SAND: SAND, WOOD: WOOD,
    COAL: COAL, IRON: IRON, PLANK: PLANK, BUSH: ITEM_BERRY, LEAF: None,
}

NAMES = {
    DIRT: "Dirt", GRASS: "Grass", STONE: "Stone", SAND: "Sand", WOOD: "Wood",
    LEAF: "Leaf", COAL: "Coal", IRON: "Iron", PLANK: "Plank",
    BUSH: "Berry Bush", ITEM_BERRY: "Berry",
}

HOTBAR = [DIRT, STONE, WOOD, PLANK, SAND, GRASS]

# ----------------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------------
def shade(c, f):
    return (max(0, min(255, int(c[0] * f))),
            max(0, min(255, int(c[1] * f))),
            max(0, min(255, int(c[2] * f))))


def build_tile_surfaces():
    """Pre-render each tile type in 4 variants (top? / right?).

    Surface size is (TILE + DEPTH) square.  The front face sits at
    y = DEPTH, and the top / right faces are the extruded quads.
    Blit at (sx, sy - DEPTH) so the front face lands exactly on the tile.
    """
    cache = {}
    for t, col in TILE_COLORS.items():
        top_col   = shade(col, 1.30)
        side_col  = shade(col, 0.66)
        front_col = col
        variants = {}
        for has_top in (0, 1):
            for has_right in (0, 1):
                s = pygame.Surface((TILE + DEPTH, TILE + DEPTH), pygame.SRCALPHA)
                # front face
                s.fill(front_col, (0, DEPTH, TILE, TILE))
                # right face
                if has_right:
                    pygame.draw.polygon(s, side_col, [
                        (TILE, DEPTH),
                        (TILE, DEPTH + TILE),
                        (TILE + DEPTH, TILE),
                        (TILE + DEPTH, 0),
                    ])
                # top face
                if has_top:
                    pygame.draw.polygon(s, top_col, [
                        (0, DEPTH),
                        (TILE, DEPTH),
                        (TILE + DEPTH, 0),
                        (DEPTH, 0),
                    ])
                variants[(has_top, has_right)] = s
        cache[t] = variants
    return cache


TILE_SURF = build_tile_surfaces()

# --- light sprite cache -----------------------------------------------------
_LIGHT_CACHE = {}

def get_light(radius):
    radius = int(radius)
    if radius in _LIGHT_CACHE:
        return _LIGHT_CACHE[radius]
    size = radius * 2
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    for i in range(radius, 0, -1):
        a = int(255 * (1.0 - i / radius) ** 1.7)
        pygame.draw.circle(s, (255, 255, 255, a), (radius, radius), i)
    _LIGHT_CACHE[radius] = s
    return s


# ----------------------------------------------------------------------------
# WORLD
# ----------------------------------------------------------------------------
class World:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.w, self.h = WORLD_W, WORLD_H
        self.tiles = [[AIR] * self.w for _ in range(self.h)]
        self.heights = [self.h // 2] * self.w
        self.generate()

    # -- generation ----------------------------------------------------------
    def generate(self):
        rng = self.rng
        h = float(self.h // 2)
        raw = []
        for _ in range(self.w):
            h += rng.uniform(-1.0, 1.0)
            h = max(28.0, min(self.h - 36.0, h))
            raw.append(h)
        for _ in range(4):  # smooth
            raw = [(raw[max(0, x - 1)] + raw[x] * 2 + raw[min(self.w - 1, x + 1)]) / 4.0
                   for x in range(self.w)]
        self.heights = [int(v) for v in raw]

        # ground layers
        for x in range(self.w):
            top = self.heights[x]
            for y in range(top, self.h):
                if y == top:
                    self.tiles[y][x] = GRASS
                elif y < top + 5 + rng.randint(0, 3):
                    self.tiles[y][x] = DIRT
                else:
                    self.tiles[y][x] = STONE

        # caves (random walks)
        for _ in range(self.w // 3):
            cx = rng.randint(3, self.w - 4)
            cy = rng.randint(self.heights[cx] + 7, self.h - 5)
            r = rng.randint(2, 5)
            for _ in range(rng.randint(25, 70)):
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        if dx * dx + dy * dy <= r * r:
                            xx, yy = cx + dx, cy + dy
                            if 0 <= xx < self.w and 0 <= yy < self.h and yy > self.heights[xx]:
                                self.tiles[yy][xx] = AIR
                cx += rng.randint(-2, 2)
                cy += rng.randint(-2, 2)
                cx = max(2, min(self.w - 3, cx))
                cy = max(self.heights[cx] + 3, min(self.h - 3, cy))

        # ores
        for _ in range(self.w * 4):
            x = rng.randint(0, self.w - 1)
            y = rng.randint(self.heights[x] + 9, self.h - 1)
            if self.tiles[y][x] == STONE:
                ore = COAL if rng.random() < 0.72 else IRON
                for dy in range(-1, 2):
                    for dx in range(-1, 2):
                        xx, yy = x + dx, y + dy
                        if 0 <= xx < self.w and 0 <= yy < self.h:
                            if self.tiles[yy][xx] == STONE and rng.random() < 0.6:
                                self.tiles[yy][xx] = ore

        # trees
        for x in range(4, self.w - 4):
            if rng.random() < 0.11:
                top = self.heights[x]
                if abs(self.heights[x - 1] - top) < 2 and abs(self.heights[x + 1] - top) < 2:
                    if self.tiles[top][x] != GRASS:
                        continue
                    self.tiles[top][x] = DIRT
                    th = rng.randint(4, 7)
                    for i in range(1, th + 1):
                        if top - i >= 0:
                            self.tiles[top - i][x] = WOOD
                    cy = top - th
                    for dy in range(-2, 3):
                        for dx in range(-2, 3):
                            if abs(dx) + abs(dy) <= 3:
                                xx, yy = x + dx, cy + dy
                                if 0 <= xx < self.w and 0 <= yy < self.h:
                                    if self.tiles[yy][xx] == AIR:
                                        self.tiles[yy][xx] = LEAF

        # berry bushes
        for x in range(3, self.w - 3):
            if rng.random() < 0.07:
                top = self.heights[x]
                if self.tiles[top][x] == GRASS and top - 1 >= 0 and self.tiles[top - 1][x] == AIR:
                    self.tiles[top - 1][x] = BUSH

    # -- accessors -----------------------------------------------------------
    def get(self, x, y):
        if y < 0:
            return AIR
        if x < 0 or x >= self.w or y >= self.h:
            return STONE
        return self.tiles[y][x]

    def set(self, x, y, t):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.tiles[y][x] = t

    def solid(self, x, y):
        return self.get(x, y) in SOLID

    def surface_y(self, x):
        x = max(0, min(self.w - 1, x))
        for y in range(self.h):
            if self.tiles[y][x] in SOLID:
                return y
        return self.h // 2


# ----------------------------------------------------------------------------
# ENTITIES
# ----------------------------------------------------------------------------
class Player:
    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)
        self.w, self.h = 18, 34
        self.vx = self.vy = 0.0
        self.on_ground = False
        self.face = 1
        self.hp, self.max_hp = 100.0, 100.0
        self.hunger, self.max_hunger = 100.0, 100.0
        self.inv = defaultdict(int)
        self.sel = 0
        self.attack_cd = 0.0
        self.mine_cd = 0.0
        self.hurt_flash = 0.0
        self.walk_t = 0.0
        self.inv[ITEM_BERRY] = 3
        self.inv[WOOD] = 5
        self.inv[PLANK] = 5


class Enemy:
    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)
        self.w, self.h = 22, 20
        self.vx = self.vy = 0.0
        self.on_ground = False
        self.hp, self.max_hp = 60.0, 60.0
        self.hurt_flash = 0.0
        self.attack_cd = 0.0
        self.face = -1

    @property
    def rect(self):
        return pygame.Rect(int(self.x), int(self.y), self.w, self.h)


class Campfire:
    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)
        self.fuel = 70.0
        self.t = 0.0


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "color", "size")

    def __init__(self, x, y, vx, vy, life, color, size=2):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.life = self.max_life = life
        self.color = color
        self.size = size


# ----------------------------------------------------------------------------
# GAME
# ----------------------------------------------------------------------------
class Game:
    def __init__(self):
        self.reset()

    # -- lifecycle -----------------------------------------------------------
    def reset(self):
        self.world = World()
        spawn_x = WORLD_W // 2
        # find a clear column
        for off in range(0, 60):
            for sgn in (1, -1):
                cx = spawn_x + off * sgn
                if 4 < cx < WORLD_W - 4:
                    sy = self.world.surface_y(cx)
                    if self.world.get(cx, sy - 1) == AIR and self.world.get(cx, sy - 2) == AIR:
                        spawn_x = cx
                        break
            else:
                continue
            break

        sy = self.world.surface_y(spawn_x)
        self.player = Player(spawn_x * TILE + 3, (sy - 2) * TILE)
        self.enemies = []
        self.campfires = []
        self.particles = []
        self.time = DAY_LEN * 0.05     # start just after dawn
        self.day = 1
        self.spawn_timer = 0.0
        self.dead = False
        self.toast_text = ""
        self.toast_t = 0.0
        self.shake = 0.0

    def toast(self, text):
        self.toast_text = text
        self.toast_t = 2.0

    # -- lighting ------------------------------------------------------------
    def light_level(self):
        t = (self.time % DAY_LEN) / DAY_LEN
        # 1.0 at noon, 0.0 at midnight
        return 0.5 + 0.5 * math.cos((t - 0.25) * 2.0 * math.pi)

    def is_night(self):
        return self.light_level() < 0.35

    # -- physics -------------------------------------------------------------
    def collide_rect(self, x, y, w, h):
        x0, y0 = int(x // TILE), int(y // TILE)
        x1, y1 = int((x + w - 0.001) // TILE), int((y + h - 0.001) // TILE)
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                if self.world.solid(tx, ty):
                    return True
        return False

    def move_and_collide(self, e, dt):
        # ---- X axis
        e.x += e.vx * dt
        if self.collide_rect(e.x, e.y, e.w, e.h):
            if e.vx > 0:
                e.x = int((e.x + e.w) // TILE) * TILE - e.w - 0.01
            elif e.vx < 0:
                e.x = (int(e.x // TILE) + 1) * TILE + 0.01
            e.vx = 0.0

        # ---- Y axis
        e.y += e.vy * dt
        e.on_ground = False
        if self.collide_rect(e.x, e.y, e.w, e.h):
            if e.vy > 0:
                e.y = int((e.y + e.h) // TILE) * TILE - e.h - 0.01
                e.on_ground = True
            elif e.vy < 0:
                e.y = (int(e.y // TILE) + 1) * TILE + 0.01
            e.vy = 0.0

    # -- world interaction ---------------------------------------------------
    def tile_center_dist(self, tx, ty):
        p = self.player
        pcx, pcy = p.x + p.w / 2, p.y + p.h / 2
        tcx, tcy = tx * TILE + TILE / 2, ty * TILE + TILE / 2
        return math.hypot(pcx - tcx, pcy - tcy)

    def in_reach(self, tx, ty, reach=6.0):
        return self.tile_center_dist(tx, ty) <= reach * TILE

    def break_tile(self, tx, ty):
        t = self.world.get(tx, ty)
        if t == AIR:
            return
        drop = DROPS.get(t)
        if drop is not None:
            self.player.inv[drop] += 1
        self.world.set(tx, ty, AIR)
        self.spawn_block_particles(tx, ty, t)
        self.shake = max(self.shake, 1.5)

    def place_tile(self, tx, ty, t):
        if self.world.get(tx, ty) not in (AIR, BUSH):
            return
        if self.player.inv[t] <= 0:
            return
        # must be adjacent to something solid (no floating sky blocks)
        adj = any(self.world.solid(tx + dx, ty + dy)
                  for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if not adj:
            return
        # don't place inside the player
        p = self.player
        trect = pygame.Rect(tx * TILE, ty * TILE, TILE, TILE)
        if trect.colliderect(pygame.Rect(int(p.x), int(p.y), p.w, p.h)):
            return
        self.player.inv[t] -= 1
        self.world.set(tx, ty, t)

    def spawn_block_particles(self, tx, ty, t):
        col = TILE_COLORS.get(t, (150, 150, 150))
        for _ in range(9):
            self.particles.append(Particle(
                tx * TILE + random.uniform(2, TILE - 2),
                ty * TILE + random.uniform(2, TILE - 2),
                random.uniform(-70, 70),
                random.uniform(-140, -20),
                random.uniform(0.3, 0.7),
                shade(col, random.uniform(0.7, 1.2)),
                random.randint(2, 3),
            ))

    def spawn_hit_particles(self, x, y, col):
        for _ in range(7):
            self.particles.append(Particle(
                x, y,
                random.uniform(-110, 110),
                random.uniform(-140, -10),
                random.uniform(0.25, 0.5),
                col,
                2,
            ))

    # -- main update ---------------------------------------------------------
    def update(self, dt, keys, mouse_buttons, mouse_world, mouse_screen):
        if self.dead:
            return

        p = self.player
        self.time += dt
        self.day = int(self.time / DAY_LEN) + 1

        # ---------------- player input / movement
        ax = 0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            ax -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            ax += 1
        if ax:
            p.face = ax
            p.walk_t += dt * 9.0
        p.vx = ax * MOVE_SPEED

        if (keys[pygame.K_SPACE] or keys[pygame.K_w] or keys[pygame.K_UP]) and p.on_ground:
            p.vy = JUMP_V
            p.on_ground = False

        p.vy = min(p.vy + GRAVITY * dt, MAX_FALL)
        self.move_and_collide(p, dt)

        # keep player inside the world
        p.x = max(0, min(WORLD_W * TILE - p.w, p.x))
        if p.y > WORLD_H * TILE:
            p.y = 0
            p.hp -= 20

        # ---------------- hunger & health
        moving = abs(p.vx) > 1
        p.hunger -= (0.55 + (0.85 if moving else 0.0)) * dt
        p.hunger = max(0.0, p.hunger)

        if p.hunger <= 0:
            p.hp -= 2.5 * dt
        elif p.hunger > 40:
            p.hp = min(p.max_hp, p.hp + 0.9 * dt)

        if p.hurt_flash > 0:
            p.hurt_flash -= dt

        # ---------------- cooldowns
        p.mine_cd = max(0.0, p.mine_cd - dt)
        p.attack_cd = max(0.0, p.attack_cd - dt)

        # ---------------- mouse actions
        mx, my = mouse_world
        tx, ty = int(mx // TILE), int(my // TILE)

        if mouse_buttons[0]:
            # attack first if an enemy is under the cursor / near player
            target = None
            for e in self.enemies:
                if e.rect.collidepoint(mx, my) and math.hypot(
                        (e.x + e.w / 2) - (p.x + p.w / 2),
                        (e.y + e.h / 2) - (p.y + p.h / 2)) < 5 * TILE:
                    target = e
                    break
            if target and p.attack_cd <= 0:
                p.attack_cd = 0.38
                target.hp -= 34
                target.hurt_flash = 0.25
                kb = 260 if target.x > p.x else -260
                target.vx += kb
                target.vy = -220
                self.spawn_hit_particles(target.x + target.w / 2,
                                         target.y + target.h / 2, (220, 70, 70))
                self.shake = max(self.shake, 3.0)
            elif not target and p.mine_cd <= 0 and self.in_reach(tx, ty):
                t = self.world.get(tx, ty)
                if t != AIR:
                    self.break_tile(tx, ty)
                    p.mine_cd = 0.16 + HARDNESS.get(t, 0.2) * 0.5

        if mouse_buttons[2] and self.in_reach(tx, ty):
            self.place_tile(tx, ty, HOTBAR[p.sel])

        # ---------------- enemies
        self.update_enemies(dt)

        # ---------------- campfires
        for c in list(self.campfires):
            c.fuel -= dt
            c.t += dt
            if c.fuel <= 0:
                self.campfires.remove(c)

        # ---------------- particles
        for pt in list(self.particles):
            pt.life -= dt
            if pt.life <= 0:
                self.particles.remove(pt)
                continue
            pt.vy += 620 * dt
            pt.x += pt.vx * dt
            pt.y += pt.vy * dt

        # ---------------- night spawning
        if self.is_night():
            self.spawn_timer -= dt
            if self.spawn_timer <= 0 and len(self.enemies) < 6:
                self.spawn_timer = random.uniform(2.5, 5.0)
                self.spawn_enemy()
        else:
            self.spawn_timer = 1.5
            # daylight burns the hounds
            for e in self.enemies:
                e.hp -= 6.0 * dt

        # ---------------- toast timer
        if self.toast_t > 0:
            self.toast_t -= dt

        self.shake = max(0.0, self.shake - dt * 12.0)

        # ---------------- death
        if p.hp <= 0:
            p.hp = 0
            self.dead = True

    def update_enemies(self, dt):
        p = self.player
        p_cx, p_cy = p.x + p.w / 2, p.y + p.h / 2

        for e in list(self.enemies):
            if e.hurt_flash > 0:
                e.hurt_flash -= dt
            e.attack_cd = max(0.0, e.attack_cd - dt)

            dx = p_cx - (e.x + e.w / 2)
            dy = p_cy - (e.y + e.h / 2)
            dist = math.hypot(dx, dy) or 1.0

            if dist > 1600:          # too far -> despawn
                self.enemies.remove(e)
                continue

            speed = 92.0
            if dist < 520:
                e.vx = (dx / dist) * speed
                e.face = 1 if dx > 0 else -1
            else:
                e.vx *= 0.9

            # jump if blocked or player is above
            if e.on_ground and (dy < -18 or abs(e.vx) < 8) and abs(dx) < 90 and random.random() < 0.09:
                e.vy = JUMP_V * 0.85

            e.vy = min(e.vy + GRAVITY * dt, MAX_FALL)
            self.move_and_collide(e, dt)

            # contact damage
            if e.rect.colliderect(pygame.Rect(int(p.x), int(p.y), p.w, p.h)):
                if e.attack_cd <= 0:
                    e.attack_cd = 0.9
                    p.hp -= 11
                    p.hurt_flash = 0.3
                    p.vy = -230
                    p.vx += (p.x - e.x) * 2.0
                    self.shake = max(self.shake, 6.0)

            if e.hp <= 0:
                self.spawn_hit_particles(e.x + e.w / 2, e.y + e.h / 2, (120, 60, 140))
                self.enemies.remove(e)

    def spawn_enemy(self):
        p = self.player
        side = random.choice((-1, 1))
        off = random.randint(SCREEN_W // 2 + 60, SCREEN_W // 2 + 320) * side
        tx = int((p.x + p.w / 2 + off) // TILE)
        tx = max(3, min(WORLD_W - 4, tx))
        ty = self.world.surface_y(tx)
        self.enemies.append(Enemy(tx * TILE + 1, (ty - 2) * TILE))

    # -- actions -------------------------------------------------------------
    def eat(self):
        p = self.player
        if p.inv[ITEM_BERRY] > 0 and p.hunger < p.max_hunger - 1:
            p.inv[ITEM_BERRY] -= 1
            p.hunger = min(p.max_hunger, p.hunger + 30)
            self.toast("You eat a berry.  (+30 hunger)")
        elif p.inv[ITEM_BERRY] <= 0:
            self.toast("No berries.")

    def place_campfire(self):
        p = self.player
        if p.inv[WOOD] < 3:
            self.toast("Need 3 wood for a campfire.")
            return
        p.inv[WOOD] -= 3
        # put it at the player's feet, snapped to the ground
        tx = int((p.x + p.w / 2) // TILE)
        ty = int((p.y + p.h) // TILE)
        while ty < WORLD_H and not self.world.solid(tx, ty):
            ty += 1
        ty -= 1
        self.campfires.append(Campfire(tx * TILE + TILE / 2, ty * TILE + TILE))
        self.toast("Campfire lit.  (-3 wood)")

    # ========================================================================
    # RENDERING
    # ========================================================================
    def draw(self, cam_x, cam_y, mouse_screen, mouse_world):
        SCREEN.fill((18, 22, 34))

        if self.shake > 0:
            cam_x += random.uniform(-self.shake, self.shake)
            cam_y += random.uniform(-self.shake, self.shake)

        self.draw_world(cam_x, cam_y)
        self.draw_entities(cam_x, cam_y)
        self.draw_particles(cam_x, cam_y)
        self.draw_darkness(cam_x, cam_y)
        self.draw_cursor(mouse_world, cam_x, cam_y)
        self.draw_ui(mouse_screen)

        if self.dead:
            self.draw_death_screen()

    # ---- world -------------------------------------------------------------
    def draw_world(self, cam_x, cam_y):
        w = self.world
        x0 = max(0, int(cam_x // TILE) - 1)
        x1 = min(w.w, int((cam_x + SCREEN_W) // TILE) + 2)
        y0 = max(0, int(cam_y // TILE) - 1)
        y1 = min(w.h, int((cam_y + SCREEN_H) // TILE) + 2)

        blit = SCREEN.blit
        for ty in range(y0, y1):
            row = w.tiles[ty]
            sy = ty * TILE - cam_y - DEPTH
            for tx in range(x1 - 1, x0 - 1, -1):
                t = row[tx]
                if t == AIR:
                    continue
                has_top = 1 if w.get(tx, ty - 1) == AIR else 0
                has_right = 1 if not w.solid(tx + 1, ty) else 0
                blit(TILE_SURF[t][(has_top, has_right)],
                     (tx * TILE - cam_x, sy))

    # ---- entities ----------------------------------------------------------
    def draw_entities(self, cam_x, cam_y):
        # campfires
        for c in self.campfires:
            sx, sy = c.x - cam_x, c.y - cam_y
            flick = 0.5 + 0.5 * math.sin(c.t * 17.0) * math.cos(c.t * 11.0)
            # stones
            pygame.draw.ellipse(SCREEN, (90, 90, 96), (sx - 15, sy - 8, 30, 12))
            # logs
            pygame.draw.line(SCREEN, (92, 60, 32), (sx - 10, sy - 3), (sx + 10, sy - 9), 4)
            pygame.draw.line(SCREEN, (78, 50, 26), (sx + 10, sy - 3), (sx - 10, sy - 9), 4)
            # flame
            fh = 16 + flick * 8
            pygame.draw.polygon(SCREEN, (255, 140, 30), [
                (sx - 7, sy - 6), (sx + 7, sy - 6), (sx, sy - 6 - fh)])
            pygame.draw.polygon(SCREEN, (255, 220, 90), [
                (sx - 4, sy - 6), (sx + 4, sy - 6), (sx, sy - 6 - fh * 0.6)])

        # enemies
        for e in self.enemies:
            self.draw_enemy(e, cam_x, cam_y)

        # player
        self.draw_player(cam_x, cam_y)

    def draw_player(self, cam_x, cam_y):
        p = self.player
        sx, sy = p.x - cam_x, p.y - cam_y

        # shadow
        pygame.draw.ellipse(SCREEN, (24, 26, 30), (sx - 3, sy + p.h - 5, p.w + 6, 8))

        skin   = (236, 202, 156) if p.hurt_flash <= 0 else (255, 120, 120)
        shirt  = (72, 112, 186)
        pants  = (56, 58, 84)
        hair   = (58, 40, 28)

        bob = math.sin(p.walk_t) * 1.6 if abs(p.vx) > 1 and p.on_ground else 0

        # legs
        pygame.draw.rect(SCREEN, pants, (sx + 2, sy + 21 + bob, 6, 13 - bob))
        pygame.draw.rect(SCREEN, pants, (sx + 10, sy + 21 - bob, 6, 13 + bob))
        # body
        pygame.draw.rect(SCREEN, shirt, (sx + 1, sy + 12, 16, 11))
        # arms
        pygame.draw.rect(SCREEN, shirt, (sx - 1, sy + 13, 4, 8))
        pygame.draw.rect(SCREEN, shirt, (sx + 15, sy + 13, 4, 8))
        # head
        pygame.draw.circle(SCREEN, skin, (sx + 9, sy + 8), 8)
        # hair
        pygame.draw.circle(SCREEN, hair, (sx + 9, sy + 5), 8)
        pygame.draw.rect(SCREEN, skin, (sx + 1, sy + 5, 16, 4))
        pygame.draw.circle(SCREEN, skin, (sx + 9, sy + 8), 8)
        pygame.draw.circle(SCREEN, hair, (sx + 9, sy + 3), 7)
        # eyes
        ex = p.face * 2
        pygame.draw.circle(SCREEN, (26, 22, 20), (sx + 9 + ex - 3, sy + 9), 2)
        pygame.draw.circle(SCREEN, (26, 22, 20), (sx + 9 + ex + 3, sy + 9), 2)

    def draw_enemy(self, e, cam_x, cam_y):
        sx, sy = e.x - cam_x, e.y - cam_y
        pygame.draw.ellipse(SCREEN, (24, 26, 30), (sx - 3, sy + e.h - 4, e.w + 6, 7))
        body = (78, 60, 92) if e.hurt_flash <= 0 else (232, 96, 96)
        # legs
        pygame.draw.rect(SCREEN, shade(body, 0.75), (sx + 2, sy + 13, 4, 7))
        pygame.draw.rect(SCREEN, shade(body, 0.75), (sx + e.w - 6, sy + 13, 4, 7))
        # body
        pygame.draw.ellipse(SCREEN, body, (sx, sy + 5, e.w, e.h - 5))
        # head
        pygame.draw.circle(SCREEN, body, (sx + e.w // 2, sy + 6), 8)
        # ears
        pygame.draw.polygon(SCREEN, shade(body, 0.7), [
            (sx + 3, sy + 1), (sx + 6, sy - 6), (sx + 9, sy + 1)])
        pygame.draw.polygon(SCREEN, shade(body, 0.7), [
            (sx + e.w - 9, sy + 1), (sx + e.w - 6, sy - 6), (sx + e.w - 3, sy + 1)])
        # eyes
        pygame.draw.circle(SCREEN, (255, 70, 70), (sx + e.w // 2 - 3, sy + 6), 2)
        pygame.draw.circle(SCREEN, (255, 70, 70), (sx + e.w // 2 + 3, sy + 6), 2)

    def draw_particles(self, cam_x, cam_y):
        for pt in self.particles:
            a = pt.life / pt.max_life
            c = shade(pt.color, 0.5 + 0.5 * a)
            pygame.draw.rect(SCREEN, c,
                             (pt.x - cam_x, pt.y - cam_y, pt.size, pt.size))

    # ---- lighting ----------------------------------------------------------
    def draw_darkness(self, cam_x, cam_y):
        light = self.light_level()
        alpha = int(218 * max(0.0, 1.0 - light) ** 1.55)
        if alpha <= 3:
            return

        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((8, 10, 32, alpha))

        p = self.player
        px = p.x + p.w / 2 - cam_x
        py = p.y + p.h / 2 - cam_y

        # player's own faint glow
        r = 110
        overlay.blit(get_light(r), (px - r, py - r), special_flags=pygame.BLEND_RGBA_SUB)

        # campfire light
        for c in self.campfires:
            flick = 0.5 + 0.5 * math.sin(c.t * 17.0) * math.cos(c.t * 11.0)
            rad = int(160 + flick * 26)
            cx = c.x - cam_x
            cy = c.y - 6 - cam_y
            overlay.blit(get_light(rad), (cx - rad, cy - rad),
                         special_flags=pygame.BLEND_RGBA_SUB)

        SCREEN.blit(overlay, (0, 0))

        # warm tint from campfires
        for c in self.campfires:
            flick = 0.5 + 0.5 * math.sin(c.t * 13.0)
            rad = int(150 + flick * 30)
            warm = pygame.Surface((rad * 2, rad * 2), pygame.SRCALPHA)
            for i in range(rad, 0, -4):
                a = int(28 * (1 - i / rad) ** 2)
                pygame.draw.circle(warm, (255, 170, 70, a), (rad, rad), i)
            cx, cy = c.x - cam_x, c.y - 6 - cam_y
            SCREEN.blit(warm, (cx - rad, cy - rad),
                        special_flags=pygame.BLEND_RGBA_ADD)

    # ---- cursor ------------------------------------------------------------
    def draw_cursor(self, mouse_world, cam_x, cam_y):
        mx, my = mouse_world
        tx, ty = int(mx // TILE), int(my // TILE)
        if not self.in_reach(tx, ty):
            return
        sx = tx * TILE - cam_x
        sy = ty * TILE - cam_y
        pygame.draw.rect(SCREEN, (255, 255, 255, 90), (sx, sy, TILE, TILE), 1)

    # ---- UI ----------------------------------------------------------------
    def draw_ui(self, mouse_screen):
        p = self.player

        # --- health & hunger bars
        self.draw_bar(20, 20, 210, 16, p.hp / p.max_hp, (196, 54, 54), (60, 20, 20), "HP")
        self.draw_bar(20, 42, 210, 16, p.hunger / p.max_hunger,
                      (222, 168, 62), (62, 48, 18), "FOOD")

        # --- day / time
        t = (self.time % DAY_LEN) / DAY_LEN
        clock = f"DAY {self.day}   {int(t*24):02d}:{int((t*24%1)*60):02d}"
        phase = "NIGHT" if self.is_night() else "DAY"
        col = (150, 180, 255) if self.is_night() else (255, 236, 170)
        SCREEN.blit(FONT_B.render(clock, True, (235, 235, 245)), (SCREEN_W - 210, 22))
        SCREEN.blit(FONT.render(phase, True, col), (SCREEN_W - 210, 44))

        # --- light meter
        light = self.light_level()
        pygame.draw.rect(SCREEN, (40, 44, 60), (SCREEN_W - 210, 66, 170, 6))
        pygame.draw.rect(SCREEN, (255, 220, 120),
                         (SCREEN_W - 210, 66, int(170 * light), 6))

        # --- hotbar
        slot_w = 52
        total = slot_w * len(HOTBAR)
        hx = SCREEN_W // 2 - total // 2
        hy = SCREEN_H - 66
        for i, t in enumerate(HOTBAR):
            x = hx + i * slot_w
            selected = (i == p.sel)
            bg = (58, 62, 82) if selected else (34, 36, 50)
            pygame.draw.rect(SCREEN, bg, (x, hy, slot_w - 6, slot_w - 6), border_radius=6)
            pygame.draw.rect(SCREEN, (150, 160, 200) if selected else (70, 74, 96),
                             (x, hy, slot_w - 6, slot_w - 6), 2, border_radius=6)
            # tile swatch (2.5D mini cube)
            sw = TILE_SURF[t][(1, 1)]
            SCREEN.blit(pygame.transform.smoothscale(sw, (30, 30)), (x + 8, hy + 2))
            cnt = p.inv.get(t, 0)
            SCREEN.blit(FONT_S.render(str(cnt), True,
                                      (240, 240, 240) if cnt else (120, 120, 130)),
                        (x + slot_w - 20, hy + slot_w - 20))
            SCREEN.blit(FONT_S.render(str(i + 1), True, (150, 150, 170)), (x + 4, hy + 2))

        # --- selected item name
        SCREEN.blit(FONT_B.render(NAMES.get(HOTBAR[p.sel], "?"), True, (230, 230, 240)),
                    (SCREEN_W // 2 - 40, hy - 22))

        # --- resources
        inv = p.inv
        lines = [
            f"Berry {inv.get(ITEM_BERRY,0):3d}   Wood {inv.get(WOOD,0):3d}   "
            f"Stone {inv.get(STONE,0):3d}",
            f"Coal  {inv.get(COAL,0):3d}   Iron {inv.get(IRON,0):3d}   "
            f"Plank {inv.get(PLANK,0):3d}",
        ]
        for i, line in enumerate(lines):
            SCREEN.blit(FONT.render(line, True, (200, 205, 220)), (20, SCREEN_H - 92 + i * 18))

        # --- hints
        hint = "E eat   C campfire(3 wood)   1-6 select   LMB mine/attack   RMB place   R restart"
        SCREEN.blit(FONT_S.render(hint, True, (140, 145, 165)), (20, SCREEN_H - 22))

        # --- toast
        if self.toast_t > 0:
            a = min(1.0, self.toast_t)
            surf = FONT_B.render(self.toast_text, True, (255, 240, 200))
            bg = pygame.Surface((surf.get_width() + 24, surf.get_height() + 14), pygame.SRCALPHA)
            bg.fill((20, 20, 30, int(190 * a)))
            x = SCREEN_W // 2 - bg.get_width() // 2
            y = SCREEN_H - 150
            SCREEN.blit(bg, (x, y))
            surf.set_alpha(int(255 * a))
            SCREEN.blit(surf, (x + 12, y + 7))

        # --- enemies counter at night
        if self.enemies:
            SCREEN.blit(FONT.render(f"Hounds: {len(self.enemies)}", True, (255, 120, 120)),
                        (SCREEN_W - 210, 84))

    def draw_bar(self, x, y, w, h, frac, col, bg, label):
        frac = max(0.0, min(1.0, frac))
        pygame.draw.rect(SCREEN, bg, (x, y, w, h), border_radius=4)
        pygame.draw.rect(SCREEN, col, (x, y, int(w * frac), h), border_radius=4)
        pygame.draw.rect(SCREEN, (25, 26, 34), (x, y, w, h), 2, border_radius=4)
        SCREEN.blit(FONT_S.render(label, True, (240, 240, 240)), (x + 6, y + 1))

    def draw_death_screen(self):
        veil = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 180))
        SCREEN.blit(veil, (0, 0))
        msg = BIG.render("YOU DIED", True, (220, 70, 70))
        SCREEN.blit(msg, (SCREEN_W // 2 - msg.get_width() // 2, SCREEN_H // 2 - 90))
        why = "starvation" if self.player.hunger <= 0 else "the hounds"
        sub = FONT_B.render(f"Survived {self.day} day(s), killed by {why}", True, (220, 220, 230))
        SCREEN.blit(sub, (SCREEN_W // 2 - sub.get_width() // 2, SCREEN_H // 2 - 20))
        r = FONT_B.render("Press  R  to wake up again      ESC to quit", True, (180, 190, 210))
        SCREEN.blit(r, (SCREEN_W // 2 - r.get_width() // 2, SCREEN_H // 2 + 30))


# ----------------------------------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------------------------------
def main():
    game = Game()
    accumulator = 0.0

    while True:
        real_dt = CLOCK.tick(60) / 1000.0
        real_dt = min(real_dt, 0.25)
        accumulator += real_dt

        # ---- events
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()
                if ev.key == pygame.K_r:
                    game.reset()
                elif not game.dead:
                    if ev.key in (pygame.K_1, pygame.K_2, pygame.K_3,
                                  pygame.K_4, pygame.K_5, pygame.K_6):
                        game.player.sel = ev.key - pygame.K_1
                    elif ev.key == pygame.K_e:
                        game.eat()
                    elif ev.key == pygame.K_c:
                        game.place_campfire()

        keys = pygame.key.get_pressed()
        mouse_buttons = pygame.mouse.get_pressed()

        # ---- camera
        p = game.player
        cam_x = p.x + p.w / 2 - SCREEN_W / 2
        cam_y = p.y + p.h / 2 - SCREEN_H / 2
        cam_x = max(0, min(WORLD_W * TILE - SCREEN_W, cam_x))
        cam_y = max(0, min(WORLD_H * TILE - SCREEN_H, cam_y))

        # ---- mouse -> world
        mouse_screen = pygame.mouse.get_pos()
        mouse_world = (mouse_screen[0] + cam_x, mouse_screen[1] + cam_y)

        # ---- fixed-step update
        steps = 0
        while accumulator >= FIXED_DT and steps < 5:
            game.update(FIXED_DT, keys, mouse_buttons, mouse_world, mouse_screen)
            accumulator -= FIXED_DT
            steps += 1
        if steps == 5:
            accumulator = 0.0

        # ---- draw
        game.draw(cam_x, cam_y, mouse_screen, mouse_world)
        pygame.display.flip()


if __name__ == "__main__":
    main()