"""
PyTerraria - a small Terraria-like 2D sandbox built with Python + Pygame.

Controls
--------
A / D  or  Left/Right  : move
Space / W / Up         : jump
Left mouse (hold)      : mine the tile under the cursor
Right mouse (hold)     : place the selected block
1..9                   : select hotbar slot
Mouse wheel            : cycle hotbar
Esc                    : quit

Requires:  pip install pygame
"""

import math
import random
import sys
from collections import deque

import pygame

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
TILE = 16
SCREEN_W, SCREEN_H = 1280, 720
FPS = 60

WORLD_W, WORLD_H = 600, 300          # in tiles
SURFACE_Y = 90                       # nominal ground level

GRAVITY = 0.55
MAX_FALL = 15.0
MOVE_SPEED = 3.2
JUMP_VEL = -9.8
REACH_TILES = 5.5

LIGHT_MARGIN = 8                     # extra tiles of light simulation off-screen

# --------------------------------------------------------------------------
# Block definitions
# --------------------------------------------------------------------------
AIR, DIRT, GRASS, STONE, WOOD, LEAVES, SAND, COAL, IRON, GOLD, DIAMOND, \
    TORCH, PLANK, GLASS, BEDROCK = range(15)

BLOCKS = {
    AIR:     dict(name="Air",     color=(0, 0, 0),       solid=False, opaque=False, hard=0.0),
    DIRT:    dict(name="Dirt",    color=(134, 96, 67),   solid=True,  opaque=True,  hard=0.30),
    GRASS:   dict(name="Grass",   color=(94, 157, 72),   solid=True,  opaque=True,  hard=0.30),
    STONE:   dict(name="Stone",   color=(128, 128, 134), solid=True,  opaque=True,  hard=0.70),
    WOOD:    dict(name="Wood",    color=(122, 84, 48),   solid=True,  opaque=True,  hard=0.45),
    LEAVES:  dict(name="Leaves",  color=(58, 124, 52),   solid=True,  opaque=True,  hard=0.15),
    SAND:    dict(name="Sand",    color=(219, 203, 140), solid=True,  opaque=True,  hard=0.25),
    COAL:    dict(name="Coal",    color=(52, 52, 58),    solid=True,  opaque=True,  hard=1.00),
    IRON:    dict(name="Iron",    color=(196, 152, 122), solid=True,  opaque=True,  hard=1.30),
    GOLD:    dict(name="Gold",    color=(224, 184, 62),  solid=True,  opaque=True,  hard=1.60),
    DIAMOND: dict(name="Diamond", color=(120, 224, 235), solid=True,  opaque=True,  hard=2.20),
    TORCH:   dict(name="Torch",   color=(245, 185, 70),  solid=False, opaque=False, hard=0.05, light=14),
    PLANK:   dict(name="Plank",   color=(160, 120, 74),  solid=True,  opaque=True,  hard=0.45),
    GLASS:   dict(name="Glass",   color=(150, 200, 215), solid=True,  opaque=False, hard=0.30),
    BEDROCK: dict(name="Bedrock", color=(48, 48, 52),    solid=True,  opaque=True,  hard=9999.0),
}

# Flat lookup tables (much faster than dict lookups in the light BFS)
SOLID      = [BLOCKS[i]["solid"]  for i in range(len(BLOCKS))]
OPAQUE     = [BLOCKS[i]["opaque"] for i in range(len(BLOCKS))]
EMIT       = [BLOCKS[i].get("light", 0) for i in range(len(BLOCKS))]
HARDNESS   = [BLOCKS[i]["hard"] for i in range(len(BLOCKS))]

# --------------------------------------------------------------------------
# Value noise helpers
# --------------------------------------------------------------------------
def _hash1(n, seed):
    n = (n * 374761393 + seed * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967295.0


def noise1(x, seed):
    i = math.floor(x)
    f = x - i
    f = f * f * (3.0 - 2.0 * f)
    a = _hash1(i, seed)
    b = _hash1(i + 1, seed)
    return a + (b - a) * f


def fbm1(x, seed, octaves=4):
    total = 0.0
    amp = 1.0
    freq = 1.0
    norm = 0.0
    for o in range(octaves):
        total += noise1(x * freq, seed + o * 7919) * amp
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


# --------------------------------------------------------------------------
# World
# --------------------------------------------------------------------------
class World:
    def __init__(self, seed):
        self.seed = seed
        self.tiles = [AIR] * (WORLD_W * WORLD_H)     # flat array: y * WORLD_W + x
        self.height = [SURFACE_Y] * WORLD_W
        self.sky = [WORLD_H] * WORLD_W               # first opaque tile per column
        self.version = 0
        self.generate()
        self.recompute_all_sky()

    # ---------------- generation ----------------
    def generate(self):
        rng = random.Random(self.seed)
        tiles = self.tiles
        ww = WORLD_W

        # --- terrain height + basic strata ---
        for x in range(ww):
            h = SURFACE_Y
            h += int((fbm1(x * 0.012, self.seed, 4) - 0.5) * 46)
            h += int((fbm1(x * 0.070, self.seed + 991, 2) - 0.5) * 8)
            h = max(30, min(WORLD_H - 90, h))
            self.height[x] = h
            for y in range(h, WORLD_H):
                if y == h:
                    tiles[y * ww + x] = GRASS
                elif y < h + 5:
                    tiles[y * ww + x] = DIRT
                else:
                    tiles[y * ww + x] = STONE

        # --- caves (cheap analytic noise) ---
        sx = self.seed * 0.0007
        sy = self.seed * 0.0011
        for x in range(ww):
            base = self.height[x] + 3
            for y in range(base, WORLD_H - 3):
                c = (math.sin(x * 0.075 + sx) * math.cos(y * 0.105 + sy)
                     + math.sin((x + y) * 0.045 + 1.7)
                     + 0.55 * math.sin(x * 0.19 - y * 0.07 + sx))
                if c > 1.05:
                    tiles[y * ww + x] = AIR

        # --- ore veins ---
        def vein(block, count, smin, smax, ymin, ymax, replace=(STONE,)):
            for _ in range(count):
                vx = rng.randrange(4, ww - 4)
                vy = rng.randrange(ymin, ymax)
                for _ in range(rng.randint(smin, smax)):
                    if 0 <= vx < ww and 0 <= vy < WORLD_H:
                        if tiles[vy * ww + vx] in replace:
                            tiles[vy * ww + vx] = block
                    vx += rng.choice((-1, 0, 0, 1))
                    vy += rng.choice((-1, 0, 0, 1))

        vein(COAL,    520, 4, 12, SURFACE_Y + 10, WORLD_H - 6, (STONE, DIRT))
        vein(IRON,    300, 3,  9, SURFACE_Y + 35, WORLD_H - 6)
        vein(GOLD,    150, 3,  7, SURFACE_Y + 85, WORLD_H - 6)
        vein(DIAMOND,  70, 2,  5, SURFACE_Y + 140, WORLD_H - 4)

        # --- trees ---
        last_tree = -10
        for x in range(4, ww - 4):
            if x - last_tree < 4:
                continue
            h = self.height[x]
            if tiles[h * ww + x] != GRASS:
                continue
            if rng.random() > 0.13:
                continue
            last_tree = x
            th = rng.randint(4, 7)
            top = h - th
            for i in range(1, th + 1):
                tiles[(h - i) * ww + x] = WOOD
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    if abs(dx) == 2 and abs(dy) == 2:
                        continue
                    lx, ly = x + dx, top + dy
                    if 0 <= lx < ww and 0 <= ly < WORLD_H:
                        if tiles[ly * ww + lx] == AIR:
                            tiles[ly * ww + lx] = LEAVES

        # --- bedrock floor ---
        for x in range(ww):
            tiles[(WORLD_H - 1) * ww + x] = BEDROCK
            if rng.random() < 0.55:
                tiles[(WORLD_H - 2) * ww + x] = BEDROCK

    # ---------------- accessors ----------------
    def get(self, x, y):
        if 0 <= x < WORLD_W and 0 <= y < WORLD_H:
            return self.tiles[y * WORLD_W + x]
        return STONE

    def is_solid(self, x, y):
        if y < 0:
            return False
        if x < 0 or x >= WORLD_W or y >= WORLD_H:
            return True
        return SOLID[self.tiles[y * WORLD_W + x]]

    def set_block(self, x, y, bid):
        if not (0 <= x < WORLD_W and 0 <= y < WORLD_H):
            return
        self.tiles[y * WORLD_W + x] = bid
        self.version += 1
        self.recompute_sky_column(x)

    def recompute_sky_column(self, x):
        for y in range(WORLD_H):
            if OPAQUE[self.tiles[y * WORLD_W + x]]:
                self.sky[x] = y
                return
        self.sky[x] = WORLD_H

    def recompute_all_sky(self):
        for x in range(WORLD_W):
            self.recompute_sky_column(x)


# --------------------------------------------------------------------------
# Lighting
# --------------------------------------------------------------------------
def compute_light(world, x0, y0, tw, th, margin=LIGHT_MARGIN):
    """Flood-fill light over a window of the world. Returns a flat list."""
    ww, wh = WORLD_W, WORLD_H
    rx0 = x0 - margin
    ry0 = y0 - margin
    rw = tw + margin * 2
    rh = th + margin * 2

    light = [0] * (rw * rh)
    q = deque()

    tiles = world.tiles
    sky = world.sky

    # 1. sky light ------------------------------------------------------
    for i in range(rw):
        wx = rx0 + i
        if wx < 0 or wx >= ww:
            continue
        sy = sky[wx]
        j0 = max(0, -ry0)
        j1 = min(rh, sy - ry0)
        for j in range(j0, j1):
            light[j * rw + i] = 15

    # 2. emitters + sky boundary seeds -----------------------------------
    for j in range(rh):
        wy = ry0 + j
        if wy < 0 or wy >= wh:
            continue
        rowbase = j * rw
        trow = wy * ww
        for i in range(rw):
            wx = rx0 + i
            if wx < 0 or wx >= ww:
                continue
            idx = rowbase + i
            lv = light[idx]
            emit = EMIT[tiles[trow + wx]]
            if emit > lv:
                lv = emit
                light[idx] = lv
                q.append(idx)
            if lv == 15:
                if ((i > 0 and light[idx - 1] != 15) or
                    (i < rw - 1 and light[idx + 1] != 15) or
                    (j > 0 and light[idx - rw] != 15) or
                    (j < rh - 1 and light[idx + rw] != 15)):
                    q.append(idx)

    # 3. BFS -------------------------------------------------------------
    while q:
        idx = q.popleft()
        cur = light[idx]
        if cur <= 1:
            continue
        i, j = divmod(idx, rw)

        # right
        if i + 1 < rw:
            nx = rx0 + i + 1
            if 0 <= nx < ww:
                nidx = idx + 1
                nlv = cur - (3 if OPAQUE[tiles[(ry0 + j) * ww + nx]] else 1)
                if nlv > light[nidx]:
                    light[nidx] = nlv
                    q.append(nidx)
        # left
        if i > 0:
            nx = rx0 + i - 1
            if 0 <= nx < ww:
                nidx = idx - 1
                nlv = cur - (3 if OPAQUE[tiles[(ry0 + j) * ww + nx]] else 1)
                if nlv > light[nidx]:
                    light[nidx] = nlv
                    q.append(nidx)
        # down
        if j + 1 < rh:
            ny = ry0 + j + 1
            if 0 <= ny < wh:
                nidx = idx + rw
                nlv = cur - (3 if OPAQUE[tiles[ny * ww + rx0 + i]] else 1)
                if nlv > light[nidx]:
                    light[nidx] = nlv
                    q.append(nidx)
        # up
        if j > 0:
            ny = ry0 + j - 1
            if 0 <= ny < wh:
                nidx = idx - rw
                nlv = cur - (3 if OPAQUE[tiles[ny * ww + rx0 + i]] else 1)
                if nlv > light[nidx]:
                    light[nidx] = nlv
                    q.append(nidx)

    return light, rx0, ry0, rw, rh


# --------------------------------------------------------------------------
# Textures
# --------------------------------------------------------------------------
def make_textures():
    tex = {}
    rng = random.Random(20240607)

    for bid, b in BLOCKS.items():
        if bid == AIR:
            continue
        surf = pygame.Surface((TILE, TILE), pygame.SRCALPHA)
        base = b["color"]

        if bid == TORCH:
            surf.fill((0, 0, 0, 0))
            pygame.draw.rect(surf, (110, 76, 42), (7, 7, 3, 9))
            pygame.draw.circle(surf, (250, 170, 40), (8, 6), 4)
            pygame.draw.circle(surf, (255, 240, 170), (8, 6), 2)
            tex[bid] = surf
            continue

        if bid == GLASS:
            surf.fill((0, 0, 0, 0))
            pygame.draw.rect(surf, (165, 215, 235, 80), (0, 0, TILE, TILE))
            pygame.draw.rect(surf, (225, 248, 255, 150), (0, 0, TILE, TILE), 1)
            tex[bid] = surf
            continue

        is_ore = bid in (COAL, IRON, GOLD, DIAMOND)
        if is_ore:
            base = BLOCKS[STONE]["color"]

        surf.fill(base)

        for _ in range(46):
            px, py = rng.randrange(TILE), rng.randrange(TILE)
            d = rng.randint(-22, 22)
            surf.set_at((px, py), (
                max(0, min(255, base[0] + d)),
                max(0, min(255, base[1] + d)),
                max(0, min(255, base[2] + d)),
            ))

        if is_ore:
            ocol = b["color"]
            for _ in range(5):
                ox, oy = rng.randrange(3, TILE - 3), rng.randrange(3, TILE - 3)
                r = rng.randint(2, 3)
                pygame.draw.circle(surf, ocol, (ox, oy), r)
                pygame.draw.circle(surf, tuple(min(255, c + 50) for c in ocol),
                                   (ox - 1, oy - 1), max(1, r - 1))

        if bid == GRASS:
            for x in range(TILE):
                surf.set_at((x, 0), (146, 205, 112))
                if rng.random() < 0.55:
                    surf.set_at((x, 1), (112, 178, 88))
        elif bid == PLANK:
            pygame.draw.line(surf, (118, 86, 50), (0, 5), (TILE - 1, 5))
            pygame.draw.line(surf, (118, 86, 50), (0, 11), (TILE - 1, 11))
        elif bid == WOOD:
            pygame.draw.line(surf, (94, 62, 34), (0, 0), (0, TILE - 1))
            pygame.draw.line(surf, (94, 62, 34), (TILE - 1, 0), (TILE - 1, TILE - 1))
        elif bid == LEAVES:
            for _ in range(26):
                px, py = rng.randrange(TILE), rng.randrange(TILE)
                surf.set_at((px, py), (40 + rng.randint(0, 55),
                                       105 + rng.randint(0, 65),
                                       40 + rng.randint(0, 40)))

        tex[bid] = surf

    return tex


# --------------------------------------------------------------------------
# Player
# --------------------------------------------------------------------------
class Player:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.w = 12
        self.h = 26
        self.vx = 0.0
        self.vy = 0.0
        self.on_ground = False

    def update(self, world, keys, dt):
        f = min(dt * 60.0, 2.0)

        ax = 0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            ax -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            ax += 1
        self.vx = ax * MOVE_SPEED

        want_jump = keys[pygame.K_SPACE] or keys[pygame.K_w] or keys[pygame.K_UP]
        if want_jump and self.on_ground:
            self.vy = JUMP_VEL
            self.on_ground = False

        self.vy += GRAVITY * f
        if self.vy > MAX_FALL:
            self.vy = MAX_FALL

        # horizontal
        self.x += self.vx * f
        self._resolve_x(world)

        # vertical
        self.y += self.vy * f
        self._resolve_y(world)

        # safety net: if stuck inside terrain, push up
        if rect_solid(world, self.x, self.y, self.w, self.h):
            for _ in range(40):
                self.y -= 1
                if not rect_solid(world, self.x, self.y, self.w, self.h):
                    break

    def _resolve_x(self, world):
        if self.vx == 0:
            return
        if rect_solid(world, self.x, self.y, self.w, self.h):
            if self.vx > 0:
                self.x = math.floor((self.x + self.w) / TILE) * TILE - self.w - 0.01
            else:
                self.x = (math.floor(self.x / TILE) + 1) * TILE + 0.01
            self.vx = 0

    def _resolve_y(self, world):
        self.on_ground = False
        if rect_solid(world, self.x, self.y, self.w, self.h):
            if self.vy > 0:
                self.y = math.floor((self.y + self.h) / TILE) * TILE - self.h - 0.01
                self.on_ground = True
            else:
                self.y = (math.floor(self.y / TILE) + 1) * TILE + 0.01
            self.vy = 0


def rect_solid(world, px, py, pw, ph):
    x0 = int(math.floor(px / TILE))
    x1 = int(math.floor((px + pw - 0.001) / TILE))
    y0 = int(math.floor(py / TILE))
    y1 = int(math.floor((py + ph - 0.001) / TILE))
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            if world.is_solid(tx, ty):
                return True
    return False


# --------------------------------------------------------------------------
# Particles
# --------------------------------------------------------------------------
def spawn_particles(particles, wx, wy, color, n=9):
    for _ in range(n):
        particles.append([
            wx + random.uniform(2, TILE - 2),
            wy + random.uniform(2, TILE - 2),
            random.uniform(-2.3, 2.3),
            random.uniform(-4.0, -0.8),
            random.uniform(0.35, 0.8),
            color,
        ])


def update_particles(particles, dt):
    alive = []
    for p in particles:
        p[3] += 0.38
        p[0] += p[2]
        p[1] += p[3]
        p[4] -= dt
        if p[4] > 0:
            alive.append(p)
    particles[:] = alive


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("PyTerraria")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 15)
    font_big = pygame.font.SysFont("consolas", 18, bold=True)

    print("Generating world...")
    seed = random.randrange(1, 10 ** 9)
    world = World(seed)
    print(f"World ready. seed={seed}  size={WORLD_W}x{WORLD_H}")

    TEX = make_textures()
    ICONS = {bid: pygame.transform.scale(s, (32, 32)) for bid, s in TEX.items()}

    # sky gradient
    grad = pygame.Surface((1, SCREEN_H))
    for y in range(SCREEN_H):
        t = y / SCREEN_H
        grad.set_at((0, y), (int(72 + 96 * t), int(126 + 92 * t), int(206 + 38 * t)))
    sky_surf = pygame.transform.scale(grad, (SCREEN_W, SCREEN_H))

    overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)

    # ---- player / spawn ----
    sx = WORLD_W // 2
    player = Player(sx * TILE + 2, (world.height[sx] - 4) * TILE)

    # ---- inventory ----
    inventory = {TORCH: 40, PLANK: 80, GLASS: 40, DIRT: 30}
    hotbar = [DIRT, GRASS, STONE, WOOD, LEAVES, PLANK, GLASS, TORCH, COAL]
    selected = 7

    particles = []
    light_cache = {"key": None, "data": None}

    mine_target = None
    mine_progress = 0.0
    place_cd = 0.0

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        dt = min(dt, 0.05)
        if place_cd > 0:
            place_cd -= dt

        # ---------------- events ----------------
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif pygame.K_1 <= event.key <= pygame.K_9:
                    idx = event.key - pygame.K_1
                    if idx < len(hotbar):
                        selected = idx
            elif event.type == pygame.MOUSEWHEEL:
                selected = (selected - event.y) % len(hotbar)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 4:
                    selected = (selected - 1) % len(hotbar)
                elif event.button == 5:
                    selected = (selected + 1) % len(hotbar)

        keys = pygame.key.get_pressed()
        mouse = pygame.mouse.get_pressed()

        # ---------------- update ----------------
        player.update(world, keys, dt)

        cam_x = player.x + player.w / 2 - SCREEN_W / 2
        cam_y = player.y + player.h / 2 - SCREEN_H / 2
        cam_x = max(0, min(WORLD_W * TILE - SCREEN_W, cam_x))
        cam_y = max(0, min(WORLD_H * TILE - SCREEN_H, cam_y))
        cam_x, cam_y = int(cam_x), int(cam_y)

        tx0 = cam_x // TILE
        ty0 = cam_y // TILE
        tw = SCREEN_W // TILE + 2
        th = SCREEN_H // TILE + 2

        # mouse -> world tile
        mpos = pygame.mouse.get_pos()
        wmx = mpos[0] + cam_x
        wmy = mpos[1] + cam_y
        ttx = int(wmx // TILE)
        tty = int(wmy // TILE)

        pcx = player.x + player.w / 2
        pcy = player.y + player.h / 2
        ddx = (ttx * TILE + TILE / 2) - pcx
        ddy = (tty * TILE + TILE / 2) - pcy
        in_reach = (ddx * ddx + ddy * ddy) <= (REACH_TILES * TILE) ** 2

        target = None
        if in_reach and 0 <= ttx < WORLD_W and 0 <= tty < WORLD_H:
            if world.tiles[tty * WORLD_W + ttx] != AIR:
                target = (ttx, tty)

        # ---- mining ----
        if mouse[0] and target is not None:
            if target != mine_target:
                mine_target = target
                mine_progress = 0.0
            bid = world.tiles[mine_target[1] * WORLD_W + mine_target[0]]
            hard = HARDNESS[bid]
            if hard >= 9999:
                mine_progress = 0.0
            else:
                mine_progress += dt / max(0.05, hard)
                if mine_progress >= 1.0:
                    bx, by = mine_target
                    drop = DIRT if bid == GRASS else bid
                    world.set_block(bx, by, AIR)
                    inventory[drop] = inventory.get(drop, 0) + 1
                    spawn_particles(particles, bx * TILE, by * TILE,
                                    BLOCKS[bid]["color"])
                    mine_target = None
                    mine_progress = 0.0
        else:
            mine_target = None
            mine_progress = 0.0

        # ---- placing ----
        if mouse[2] and place_cd <= 0 and in_reach:
            if 0 <= ttx < WORLD_W and 0 <= tty < WORLD_H:
                if world.tiles[tty * WORLD_W + ttx] == AIR:
                    bid = hotbar[selected]
                    if inventory.get(bid, 0) > 0:
                        px0, py0 = ttx * TILE, tty * TILE
                        overlaps = not (player.x + player.w <= px0 or
                                        player.x >= px0 + TILE or
                                        player.y + player.h <= py0 or
                                        player.y >= py0 + TILE)
                        if not (overlaps and SOLID[bid]):
                            world.set_block(ttx, tty, bid)
                            inventory[bid] -= 1
                            place_cd = 0.13

        update_particles(particles, dt)

        # ---------------- lighting ----------------
        key = (tx0, ty0, world.version)
        if light_cache["key"] != key:
            light_cache["data"] = compute_light(world, tx0, ty0, tw, th)
            light_cache["key"] = key
        light, lx0, ly0, lrw, lrh = light_cache["data"]

        # ---------------- render ----------------
        screen.blit(sky_surf, (0, 0))

        # tiles
        for ty in range(ty0, ty0 + th):
            if ty < 0 or ty >= WORLD_H:
                continue
            row = ty * WORLD_W
            sy_px = ty * TILE - cam_y
            for tx in range(tx0, tx0 + tw):
                if tx < 0 or tx >= WORLD_W:
                    continue
                bid = world.tiles[row + tx]
                if bid == AIR:
                    continue
                screen.blit(TEX[bid], (tx * TILE - cam_x, sy_px))

        # particles
        for p in particles:
            a = max(0, min(255, int(255 * (p[4] / 0.8))))
            c = p[5]
            s = pygame.Surface((3, 3), pygame.SRCALPHA)
            s.fill((c[0], c[1], c[2], a))
            screen.blit(s, (int(p[0] - cam_x), int(p[1] - cam_y)))

        # player
        px, py = int(player.x - cam_x), int(player.y - cam_y)
        pygame.draw.rect(screen, (46, 60, 118), (px, py + 18, player.w, player.h - 18))
        pygame.draw.rect(screen, (72, 112, 202), (px, py + 10, player.w, 10))
        pygame.draw.rect(screen, (236, 196, 156), (px, py, player.w, 11))
        pygame.draw.rect(screen, (92, 56, 30), (px, py, player.w, 4))
        pygame.draw.rect(screen, (28, 28, 28), (px + 3, py + 5, 2, 2))
        pygame.draw.rect(screen, (28, 28, 28), (px + 7, py + 5, 2, 2))

        # darkness overlay
        overlay.fill((0, 0, 0, 0))
        for ty in range(ty0, ty0 + th):
            j = ty - ly0
            if j < 0 or j >= lrh:
                continue
            rowbase = j * lrw
            sy_px = ty * TILE - cam_y
            for tx in range(tx0, tx0 + tw):
                i = tx - lx0
                if i < 0 or i >= lrw:
                    continue
                lv = light[rowbase + i]
                if lv >= 15:
                    continue
                a = 250 - lv * 16
                if a <= 4:
                    continue
                pygame.draw.rect(overlay, (0, 0, 0, a),
                                 (tx * TILE - cam_x, sy_px, TILE, TILE))
        screen.blit(overlay, (0, 0))

        # target highlight
        if target is not None:
            hx = target[0] * TILE - cam_x
            hy = target[1] * TILE - cam_y
            pygame.draw.rect(screen, (255, 255, 255), (hx, hy, TILE, TILE), 1)
            if mine_progress > 0:
                bar_w = int(TILE * min(1.0, mine_progress))
                pygame.draw.rect(screen, (30, 30, 30), (hx, hy - 6, TILE, 4))
                pygame.draw.rect(screen, (255, 220, 90), (hx, hy - 6, bar_w, 4))

        # ---------------- UI ----------------
        slot = 48
        total = len(hotbar) * slot
        start_x = (SCREEN_W - total) // 2
        bar_y = SCREEN_H - slot - 14

        panel = pygame.Surface((total + 12, slot + 12), pygame.SRCALPHA)
        panel.fill((10, 12, 20, 170))
        screen.blit(panel, (start_x - 6, bar_y - 6))

        for i, bid in enumerate(hotbar):
            x = start_x + i * slot
            sel = (i == selected)
            pygame.draw.rect(screen, (60, 66, 84) if not sel else (230, 200, 90),
                             (x, bar_y, slot, slot), 2)
            if bid in ICONS:
                screen.blit(ICONS[bid], (x + 8, bar_y + 8))
            cnt = inventory.get(bid, 0)
            if cnt > 0:
                txt = font.render(str(cnt), True, (255, 255, 255))
                screen.blit(txt, (x + slot - txt.get_width() - 4,
                                  bar_y + slot - txt.get_height() - 2))
            num = font.render(str(i + 1), True, (150, 155, 170))
            screen.blit(num, (x + 3, bar_y + 1))

        sel_bid = hotbar[selected]
        name = BLOCKS[sel_bid]["name"]
        label = font_big.render(name, True, (245, 245, 250))
        screen.blit(label, (SCREEN_W // 2 - label.get_width() // 2, bar_y - 34))

        info = font.render(
            f"seed {seed}   fps {int(clock.get_fps())}   "
            f"pos {int(player.x // TILE)},{int(player.y // TILE)}   "
            f"depth {int(player.y // TILE) - SURFACE_Y}",
            True, (215, 220, 235))
        screen.blit(info, (10, 8))

        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()