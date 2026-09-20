"""
PyStarve - a Don't Starve style survival game built with Python + Pygame.

Controls
--------
W A S D / Arrows   : move
Left Mouse         : chop / mine / pick / attack (aim at a target)
Right Mouse        : use or place the selected item
1 .. 0             : select inventory slot
Mouse Wheel / Q E  : cycle slots
C                  : toggle crafting menu (click a recipe to craft)
Space              : same as left click at the cursor
R                  : restart after death
Esc                : quit

Requires:  pip install pygame
"""

import math
import random
import sys

import pygame

# ==========================================================================
# Configuration
# ==========================================================================
TILE = 32
SCREEN_W, SCREEN_H = 1280, 720
FPS = 60
WORLD_W, WORLD_H = 180, 180          # tiles
DAY_LEN, DUSK_LEN, NIGHT_LEN = 46.0, 16.0, 30.0
CYCLE_LEN = DAY_LEN + DUSK_LEN + NIGHT_LEN

REACH = 86.0
ACTION_CD = 0.38

# ==========================================================================
# Ground types
# ==========================================================================
G_GRASS, G_FOREST, G_ROCK, G_SAND, G_SWAMP = range(5)

GROUND_COLORS = {
    G_GRASS:  (100, 150, 68),
    G_FOREST: (62, 106, 52),
    G_ROCK:   (118, 118, 124),
    G_SAND:   (224, 206, 148),
    G_SWAMP:  (80, 90, 64),
}

# ==========================================================================
# Items
# ==========================================================================
ITEMS = {
    'log':      dict(name='Log',          color=(122, 84, 48),  stack=20),
    'twigs':    dict(name='Twigs',        color=(164, 124, 78), stack=20),
    'grass':    dict(name='Cut Grass',    color=(150, 192, 82), stack=20),
    'flint':    dict(name='Flint',        color=(92, 92, 100),  stack=20),
    'rocks':    dict(name='Rocks',        color=(144, 144, 150),stack=20),
    'gold':     dict(name='Gold Nugget',  color=(226, 186, 62), stack=20),
    'berries':  dict(name='Berries',      color=(198, 58, 70),  stack=20, food=14),
    'carrot':   dict(name='Carrot',       color=(232, 142, 52), stack=20, food=20),
    'meat':     dict(name='Monster Meat', color=(148, 74, 84),  stack=20, food=16, sanity=-12),
    'petals':   dict(name='Petals',       color=(232, 152, 200),stack=20, sanity=6),
    'torch':    dict(name='Torch',        color=(250, 190, 80), stack=1),
    'campfire': dict(name='Campfire',     color=(240, 140, 60), stack=4),
    'axe':      dict(name='Axe',          color=(206, 206, 214),stack=1),
    'pickaxe':  dict(name='Pickaxe',      color=(184, 196, 208),stack=1),
    'spear':    dict(name='Spear',        color=(206, 172, 122),stack=1),
    'logsuit':  dict(name='Log Suit',     color=(142, 102, 62), stack=1),
}

RECIPES = [
    ('torch',    {'twigs': 2, 'grass': 2}),
    ('campfire', {'log': 2, 'grass': 3}),
    ('axe',      {'twigs': 1, 'flint': 1}),
    ('pickaxe',  {'twigs': 2, 'flint': 2}),
    ('spear',    {'twigs': 2, 'flint': 1, 'grass': 2}),
    ('logsuit',  {'log': 8, 'grass': 6}),
]

TOOL_ITEMS = {'axe', 'pickaxe', 'spear', 'logsuit'}

# ==========================================================================
# Noise
# ==========================================================================
def _h2(ix, iy, seed):
    n = (ix * 374761393 + iy * 668265263 + seed * 144665) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967295.0


def noise2(x, y, seed):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    fx = fx * fx * (3.0 - 2.0 * fx)
    fy = fy * fy * (3.0 - 2.0 * fy)
    a = _h2(ix, iy, seed)
    b = _h2(ix + 1, iy, seed)
    c = _h2(ix, iy + 1, seed)
    d = _h2(ix + 1, iy + 1, seed)
    top = a + (b - a) * fx
    bot = c + (d - c) * fx
    return top + (bot - top) * fy


def fbm2(x, y, seed, octaves=3):
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        total += noise2(x * freq, y * freq, seed + o * 7919) * amp
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


# ==========================================================================
# World
# ==========================================================================
class World:
    def __init__(self, seed):
        self.seed = seed
        self.ground = bytearray(WORLD_W * WORLD_H)
        self.resources = []
        self.enemies = []
        self.campfires = []
        self.particles = []
        self.lights = []          # [(x, y, radius, intensity)]
        self.rng = random.Random(seed)
        self.generate()

    # ---------------- generation ----------------
    def generate(self):
        rng = self.rng
        gw, gh = WORLD_W, WORLD_H

        # --- biome map ---
        for y in range(gh):
            for x in range(gw):
                temp = fbm2(x * 0.021, y * 0.021, self.seed, 3)
                moist = fbm2(x * 0.019 + 311.0, y * 0.019 + 177.0, self.seed + 5051, 3)
                if temp > 0.60 and moist < 0.44:
                    g = G_SAND
                elif moist > 0.63:
                    g = G_SWAMP
                elif temp < 0.36:
                    g = G_ROCK
                elif moist > 0.50 and temp < 0.58:
                    g = G_FOREST
                else:
                    g = G_GRASS
                self.ground[y * gw + x] = g

        # --- scatter resources ---
        for y in range(3, gh - 3):
            for x in range(3, gw - 3):
                g = self.ground[y * gw + x]
                r = rng.random()
                wx = x * TILE + TILE / 2 + rng.uniform(-8, 8)
                wy = y * TILE + TILE / 2 + rng.uniform(-8, 8)

                if g == G_FOREST:
                    if r < 0.16:
                        self.resources.append(Resource(wx, wy, 'tree', rng))
                    elif r < 0.20:
                        self.resources.append(Resource(wx, wy, 'sapling', rng))
                    elif r < 0.235:
                        self.resources.append(Resource(wx, wy, 'bush', rng))
                    elif r < 0.265:
                        self.resources.append(Resource(wx, wy, 'grass', rng))
                elif g == G_GRASS:
                    if r < 0.045:
                        self.resources.append(Resource(wx, wy, 'tree', rng))
                    elif r < 0.075:
                        self.resources.append(Resource(wx, wy, 'grass', rng))
                    elif r < 0.098:
                        self.resources.append(Resource(wx, wy, 'bush', rng))
                    elif r < 0.118:
                        self.resources.append(Resource(wx, wy, 'sapling', rng))
                    elif r < 0.140:
                        self.resources.append(Resource(wx, wy, 'flower', rng))
                    elif r < 0.152:
                        self.resources.append(Resource(wx, wy, 'carrot', rng))
                elif g == G_ROCK:
                    if r < 0.115:
                        self.resources.append(Resource(wx, wy, 'rock', rng))
                    elif r < 0.132:
                        self.resources.append(Resource(wx, wy, 'goldrock', rng))
                    elif r < 0.150:
                        self.resources.append(Resource(wx, wy, 'flint', rng))
                elif g == G_SWAMP:
                    if r < 0.055:
                        self.resources.append(Resource(wx, wy, 'tree', rng))
                    elif r < 0.085:
                        self.resources.append(Resource(wx, wy, 'grass', rng))
                    elif r < 0.105:
                        self.resources.append(Resource(wx, wy, 'bush', rng))
                elif g == G_SAND:
                    if r < 0.030:
                        self.resources.append(Resource(wx, wy, 'rock', rng))
                    elif r < 0.055:
                        self.resources.append(Resource(wx, wy, 'grass', rng))

    # ---------------- helpers ----------------
    def ground_at(self, tx, ty):
        if 0 <= tx < WORLD_W and 0 <= ty < WORLD_H:
            return self.ground[ty * WORLD_W + tx]
        return G_ROCK

    def add_particles(self, x, y, color, n=8, spread=2.6):
        for _ in range(n):
            self.particles.append([
                x + random.uniform(-4, 4), y + random.uniform(-4, 4),
                random.uniform(-spread, spread), random.uniform(-spread, 0.6),
                random.uniform(0.3, 0.65), color,
            ])

    def spawn_hound(self, px, py):
        ang = random.uniform(0, math.tau)
        d = random.uniform(430, 560)
        x = px + math.cos(ang) * d
        y = py + math.sin(ang) * d
        x = max(TILE * 2, min(WORLD_W * TILE - TILE * 2, x))
        y = max(TILE * 2, min(WORLD_H * TILE - TILE * 2, y))
        self.enemies.append(Enemy(x, y, 'hound'))

    def spawn_spider(self, px, py):
        ang = random.uniform(0, math.tau)
        d = random.uniform(320, 460)
        x = max(TILE * 2, min(WORLD_W * TILE - TILE * 2, px + math.cos(ang) * d))
        y = max(TILE * 2, min(WORLD_H * TILE - TILE * 2, py + math.sin(ang) * d))
        self.enemies.append(Enemy(x, y, 'spider'))


# ==========================================================================
# Resources
# ==========================================================================
RES_DEFS = {
    'tree':     dict(hp=5,  radius=14, solid=True,  work='chop',
                     drops=[('log', 2, 3), ('twigs', 0, 1)]),
    'rock':     dict(hp=4,  radius=15, solid=True,  work='mine',
                     drops=[('rocks', 2, 3), ('flint', 1, 2)]),
    'goldrock': dict(hp=6,  radius=15, solid=True,  work='mine',
                     drops=[('rocks', 1, 2), ('gold', 1, 2)]),
    'bush':     dict(hp=1,  radius=12, solid=False, work='pick',
                     drops=[('berries', 1, 2)], regrow=22.0),
    'grass':    dict(hp=1,  radius=11, solid=False, work='pick',
                     drops=[('grass', 1, 2)], regrow=16.0),
    'sapling':  dict(hp=1,  radius=11, solid=False, work='pick',
                     drops=[('twigs', 1, 2)], regrow=16.0),
    'flower':   dict(hp=1,  radius=10, solid=False, work='pick',
                     drops=[('petals', 1, 2)], regrow=26.0),
    'carrot':   dict(hp=1,  radius=10, solid=False, work='pick',
                     drops=[('carrot', 1, 1)], regrow=34.0),
    'flint':    dict(hp=1,  radius=10, solid=False, work='pick',
                     drops=[('flint', 1, 1)], regrow=40.0),
}


class Resource:
    def __init__(self, x, y, kind, rng):
        d = RES_DEFS[kind]
        self.x = float(x)
        self.y = float(y)
        self.kind = kind
        self.max_hp = d['hp']
        self.hp = d['hp']
        self.radius = d['radius']
        self.solid = d['solid']
        self.work = d['work']
        self.drops = d['drops']
        self.regrow = d.get('regrow', 0)
        self.respawn_t = 0.0
        self.hit_flash = 0.0
        self.dead = False
        self.scale = rng.uniform(0.88, 1.22)
        self.phase = rng.uniform(0, math.tau)
        self.dead = False

    @property
    def harvestable(self):
        return self.respawn_t <= 0.0

    def update(self, dt):
        if self.hit_flash > 0:
            self.hit_flash -= dt
        if self.respawn_t > 0:
            self.respawn_t -= dt

    def damage(self, amount):
        self.hp -= amount
        self.hit_flash = 0.14
        if self.hp <= 0:
            if self.regrow > 0:
                self.hp = self.max_hp
                self.respawn_t = self.regrow
                return False      # not destroyed
            self.dead = True
            return True
        return False


# ==========================================================================
# Enemies
# ==========================================================================
ENEMY_DEFS = {
    'spider': dict(hp=32, speed=1.5, dmg=8,  radius=14, aggro=340, reach=26,
                   color=(52, 44, 62), drops=[('meat', 1, 2)]),
    'hound':  dict(hp=55, speed=2.6, dmg=15, radius=15, aggro=520, reach=28,
                   color=(126, 82, 52), drops=[('meat', 1, 2)]),
}


class Enemy:
    def __init__(self, x, y, kind):
        d = ENEMY_DEFS[kind]
        self.x = float(x)
        self.y = float(y)
        self.kind = kind
        self.max_hp = d['hp']
        self.hp = d['hp']
        self.speed = d['speed']
        self.damage = d['dmg']
        self.radius = d['radius']
        self.aggro = d['aggro']
        self.reach = d['reach']
        self.drops = d['drops']
        self.attack_cd = 0.0
        self.hit_flash = 0.0
        self.dead = False
        self.wobble = random.uniform(0, math.tau)
        self.facing = 1

    def update(self, dt, player, world):
        if self.hit_flash > 0:
            self.hit_flash -= dt
        if self.attack_cd > 0:
            self.attack_cd -= dt

        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.hypot(dx, dy) or 1.0
        self.facing = 1 if dx >= 0 else -1
        self.wobble += dt * 9.0

        if dist < self.aggro:
            if dist > self.reach:
                self.x += dx / dist * self.speed
                self.y += dy / dist * self.speed
            elif self.attack_cd <= 0:
                player.take_damage(self.damage)
                self.attack_cd = 1.0

        # separation from other enemies
        for other in world.enemies:
            if other is self or other.dead:
                continue
            ox = self.x - other.x
            oy = self.y - other.y
            od = math.hypot(ox, oy)
            if 0 < od < self.radius + other.radius:
                push = (self.radius + other.radius - od) * 0.5
                self.x += ox / od * push
                self.y += oy / od * push

        self.x = max(TILE, min(WORLD_W * TILE - TILE, self.x))
        self.y = max(TILE, min(WORLD_H * TILE - TILE, self.y))


# ==========================================================================
# Player
# ==========================================================================
class Player:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.radius = 12
        self.vx = 0.0
        self.vy = 0.0
        self.facing = 1
        self.health = 100.0
        self.max_health = 100.0
        self.hunger = 100.0
        self.max_hunger = 100.0
        self.sanity = 100.0
        self.max_sanity = 100.0
        self.tools = set()
        self.inv = {}                # item -> count
        self.slots = []              # ordered item names
        self.selected = 0
        self.action_cd = 0.0
        self.swing = 0.0
        self.hurt_flash = 0.0
        self.torch_time = 0.0
        self.torch_on = False
        self.bob = 0.0
        self.dead = False

    # ---------------- inventory ----------------
    def add_item(self, name, count=1):
        if name not in self.inv:
            if len(self.slots) >= 10:
                return False
            self.slots.append(name)
            self.inv[name] = 0
        cap = ITEMS[name].get('stack', 20)
        self.inv[name] = min(cap, self.inv[name] + count)
        return True

    def remove_item(self, name, count=1):
        if self.inv.get(name, 0) < count:
            return False
        self.inv[name] -= count
        if self.inv[name] <= 0:
            del self.inv[name]
            if name in self.slots:
                self.slots.remove(name)
            self.selected = max(0, min(self.selected, max(0, len(self.slots) - 1)))
        return True

    def count(self, name):
        return self.inv.get(name, 0)

    def selected_item(self):
        if not self.slots:
            return None
        self.selected = max(0, min(self.selected, len(self.slots) - 1))
        return self.slots[self.selected]

    def can_craft(self, recipe):
        return all(self.count(k) >= v for k, v in recipe.items())

    def craft(self, name):
        for rname, ing in RECIPES:
            if rname != name:
                continue
            if not self.can_craft(ing):
                return False
            for k, v in ing.items():
                self.remove_item(k, v)
            if name in TOOL_ITEMS:
                self.tools.add(name)
            else:
                self.add_item(name, 1)
            return True
        return False

    # ---------------- stats ----------------
    def take_damage(self, amount):
        armor = 0.35 if 'logsuit' in self.tools else 0.0
        self.health -= amount * (1.0 - armor)
        self.hurt_flash = 0.30
        if self.health <= 0:
            self.health = 0
            self.dead = True

    def eat(self, name):
        info = ITEMS[name]
        if 'food' not in info and 'sanity' not in info:
            return False
        if not self.remove_item(name, 1):
            return False
        self.hunger = min(self.max_hunger, self.hunger + info.get('food', 0))
        self.sanity = max(0, min(self.max_sanity, self.sanity + info.get('sanity', 0)))
        if 'sanity' not in info and info.get('food', 0) > 0:
            self.sanity = min(self.max_sanity, self.sanity + 2)
        return True

    # ---------------- movement ----------------
    def update(self, dt, keys, world):
        if self.action_cd > 0:
            self.action_cd -= dt
        if self.swing > 0:
            self.swing -= dt * 4.0
        if self.hurt_flash > 0:
            self.hurt_flash -= dt

        mvx = 0.0
        mvy = 0.0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            mvx -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            mvx += 1
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            mvy -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            mvy += 1

        if mvx or mvy:
            ln = math.hypot(mvx, mvy)
            mvx /= ln
            mvy /= ln

        speed = 2.9
        self.vx = mvx * speed
        self.vy = mvy * speed
        if mvx:
            self.facing = 1 if mvx > 0 else -1

        moving = bool(mvx or mvy)
        if moving:
            self.bob += dt * 11.0

        # move + collide with solid resources
        nx = self.x + self.vx * dt * 60.0
        if not self._collides(world, nx, self.y):
            self.x = nx
        ny = self.y + self.vy * dt * 60.0
        if not self._collides(world, self.x, ny):
            self.y = ny

        self.x = max(TILE, min(WORLD_W * TILE - TILE, self.x))
        self.y = max(TILE, min(WORLD_H * TILE - TILE, self.y))

        # hunger drain
        drain = 0.55 + (0.85 if moving else 0.0)
        self.hunger -= drain * dt
        if self.hunger <= 0:
            self.hunger = 0
            self.take_damage(3.0 * dt)
        elif self.hunger > 45 and self.health < self.max_health:
            self.health = min(self.max_health, self.health + 1.2 * dt)

        # torch fuel
        if self.torch_on:
            self.torch_time -= dt
            if self.torch_time <= 0:
                self.torch_on = False
                self.torch_time = 0

    def _collides(self, world, nx, ny):
        for res in world.resources:
            if not res.solid or res.respawn_t > 0:
                continue
            dx = nx - res.x
            dy = ny - res.y
            r = res.radius + self.radius
            if dx * dx + dy * dy < r * r * 0.72:
                return True
        return False


# ==========================================================================
# Ground textures
# ==========================================================================
def make_ground_textures():
    rng = random.Random(99117)
    tex = {}
    for g, base in GROUND_COLORS.items():
        variants = []
        for _ in range(4):
            s = pygame.Surface((TILE, TILE))
            s.fill(base)
            for _ in range(60):
                px, py = rng.randrange(TILE), rng.randrange(TILE)
                d = rng.randint(-16, 16)
                s.set_at((px, py), (
                    max(0, min(255, base[0] + d)),
                    max(0, min(255, base[1] + d)),
                    max(0, min(255, base[2] + d)),
                ))
            # small decorative specks
            for _ in range(4):
                px, py = rng.randrange(2, TILE - 2), rng.randrange(2, TILE - 2)
                c = (max(0, base[0] - 26), max(0, base[1] - 26), max(0, base[2] - 26))
                pygame.draw.rect(s, c, (px, py, 2, 2))
            variants.append(s)
        tex[g] = variants
    return tex


# ==========================================================================
# Light blobs (cached)
# ==========================================================================
_LIGHT_CACHE = {}


def light_blob(size):
    size = max(16, int(size))
    key = size
    if key in _LIGHT_CACHE:
        return _LIGHT_CACHE[key]

    n = 48
    base = pygame.Surface((n, n), pygame.SRCALPHA)
    c = n / 2.0
    for y in range(n):
        for x in range(n):
            d = math.hypot(x - c + 0.5, y - c + 0.5) / c
            a = int(255 * max(0.0, 1.0 - d) ** 1.9)
            base.set_at((x, y), (0, 0, 0, a))
    surf = pygame.transform.smoothscale(base, (size, size))
    _LIGHT_CACHE[key] = surf
    return surf


# ==========================================================================
# Drawing helpers
# ==========================================================================
def draw_resource(surf, res, cam_x, cam_y, t):
    x = res.x - cam_x
    y = res.y - cam_y
    if x < -80 or x > SCREEN_W + 80 or y < -120 or y > SCREEN_H + 120:
        return

    harvestable = res.harvestable
    flash = res.hit_flash > 0
    s = res.scale

    if flash:
        tint = pygame.Surface((60, 60), pygame.SRCALPHA)

    if res.kind == 'tree':
        sway = math.sin(t * 1.6 + res.phase) * 2.0
        trunk_h = 34 * s
        pygame.draw.rect(surf, (86, 58, 34),
                         (x - 5, y - trunk_h, 10, trunk_h))
        pygame.draw.rect(surf, (108, 74, 44),
                         (x - 5, y - trunk_h, 4, trunk_h))
        col = (46, 96, 44) if not flash else (150, 200, 140)
        pygame.draw.circle(surf, col, (int(x + sway), int(y - trunk_h - 12)), int(20 * s))
        pygame.draw.circle(surf, (58, 116, 52),
                           (int(x - 12 * s + sway), int(y - trunk_h - 4)), int(14 * s))
        pygame.draw.circle(surf, (58, 116, 52),
                           (int(x + 12 * s + sway), int(y - trunk_h - 6)), int(15 * s))
        pygame.draw.circle(surf, (74, 138, 62),
                           (int(x + sway * 0.5), int(y - trunk_h - 20)), int(12 * s))

    elif res.kind in ('rock', 'goldrock'):
        col = (128, 128, 136) if not flash else (200, 200, 210)
        pts = [(x - 15 * s, y + 6), (x - 11 * s, y - 12 * s),
               (x - 2 * s, y - 17 * s), (x + 9 * s, y - 12 * s),
               (x + 15 * s, y + 2), (x + 8 * s, y + 8)]
        pygame.draw.polygon(surf, col, pts)
        pygame.draw.polygon(surf, (98, 98, 106), pts, 2)
        pygame.draw.polygon(surf, (160, 160, 168),
                            [(x - 11 * s, y - 12 * s), (x - 2 * s, y - 17 * s),
                             (x + 2 * s, y - 8 * s), (x - 7 * s, y - 5 * s)])
        if res.kind == 'goldrock':
            for i in range(5):
                gx = x + math.cos(i * 2.1) * 8 * s
                gy = y - 4 + math.sin(i * 2.1) * 6 * s
                pygame.draw.circle(surf, (236, 196, 70), (int(gx), int(gy)), 3)

    elif res.kind == 'bush':
        if harvestable:
            pygame.draw.circle(surf, (52, 104, 48), (int(x), int(y - 6)), int(13 * s))
            pygame.draw.circle(surf, (68, 126, 58), (int(x - 4), int(y - 10)), int(9 * s))
            for i in range(3):
                bx = x + math.cos(i * 2.4 + res.phase) * 7
                by = y - 7 + math.sin(i * 2.4 + res.phase) * 5
                pygame.draw.circle(surf, (198, 58, 70), (int(bx), int(by)), 3)
        else:
            pygame.draw.circle(surf, (58, 84, 50), (int(x), int(y - 4)), int(9 * s))

    elif res.kind == 'grass':
        if harvestable:
            for i in range(6):
                a = -math.pi / 2 + (i - 2.5) * 0.26
                ex = x + math.cos(a) * 13 * s
                ey = y + math.sin(a) * 13 * s
                pygame.draw.line(surf, (128, 176, 68), (x, y), (ex, ey), 3)
            pygame.draw.line(surf, (150, 198, 82), (x, y), (x, y - 15 * s), 3)
        else:
            pygame.draw.line(surf, (108, 132, 62), (x - 5, y), (x, y - 4), 3)
            pygame.draw.line(surf, (108, 132, 62), (x + 5, y), (x, y - 4), 3)

    elif res.kind == 'sapling':
        if harvestable:
            for i, a in enumerate((-0.5, 0.0, 0.5)):
                ex = x + math.sin(a) * 12 * s
                ey = y - math.cos(a) * 20 * s
                pygame.draw.line(surf, (148, 112, 66), (x, y), (ex, ey), 3)
            pygame.draw.circle(surf, (176, 140, 84), (int(x), int(y - 21 * s)), 3)
        else:
            pygame.draw.line(surf, (108, 84, 54), (x, y), (x, y - 5), 3)

    elif res.kind == 'flower':
        if harvestable:
            pygame.draw.line(surf, (74, 132, 62), (x, y), (x, y - 12 * s), 2)
            c = [(232, 152, 200), (240, 210, 110), (200, 140, 230)][int(res.phase) % 3]
            for i in range(5):
                a = i * math.tau / 5
                pygame.draw.circle(surf, c,
                                   (int(x + math.cos(a) * 5), int(y - 12 * s + math.sin(a) * 5)), 4)
            pygame.draw.circle(surf, (250, 236, 160), (int(x), int(y - 12 * s)), 3)

    elif res.kind == 'carrot':
        if harvestable:
            for i in range(4):
                a = -math.pi / 2 + (i - 1.5) * 0.4
                pygame.draw.line(surf, (74, 148, 66), (x, y),
                                 (x + math.cos(a) * 13 * s, y + math.sin(a) * 13 * s), 3)
            pygame.draw.circle(surf, (232, 142, 52), (int(x), int(y + 3)), 5)

    elif res.kind == 'flint':
        if harvestable:
            pygame.draw.polygon(surf, (92, 92, 100),
                                [(x - 8, y + 3), (x, y - 8), (x + 8, y + 2), (x + 1, y + 6)])
            pygame.draw.polygon(surf, (140, 140, 150),
                                [(x - 5, y + 1), (x, y - 6), (x + 3, y - 1)])


def draw_enemy(surf, e, cam_x, cam_y, t):
    x = e.x - cam_x
    y = e.y - cam_y
    if x < -60 or x > SCREEN_W + 60 or y < -60 or y > SCREEN_H + 60:
        return

    flash = e.hit_flash > 0
    d = ENEMY_DEFS[e.kind]

    # shadow
    pygame.draw.ellipse(surf, (0, 0, 0, 60),
                        (x - e.radius, y + e.radius * 0.5, e.radius * 2, e.radius * 0.8))

    if e.kind == 'spider':
        col = (210, 190, 220) if flash else d['color']
        for i in range(4):
            for side in (-1, 1):
                ang = -0.5 + i * 0.55
                lx = x + side * math.cos(ang) * 16
                ly = y + 3 + math.sin(ang) * 8 + math.sin(e.wobble + i) * 1.6
                pygame.draw.line(surf, col, (x, y), (lx, ly), 2)
        pygame.draw.circle(surf, col, (int(x), int(y)), int(e.radius))
        pygame.draw.circle(surf, (30, 24, 36), (int(x), int(y - 2)), int(e.radius) - 4)
        for side in (-1, 1):
            ex = x + side * 4
            pygame.draw.circle(surf, (230, 60, 60), (int(ex), int(y - 4)), 2)
    else:  # hound
        col = (240, 220, 200) if flash else d['color']
        body_y = y + math.sin(e.wobble) * 1.2
        pygame.draw.ellipse(surf, col,
                            (x - e.radius, body_y - 9, e.radius * 2, 20))
        hx = x + e.facing * e.radius * 0.75
        pygame.draw.circle(surf, col, (int(hx), int(body_y - 8)), 9)
        pygame.draw.polygon(surf, (86, 56, 34),
                            [(hx - 6, body_y - 14), (hx - 2, body_y - 20), (hx + 1, body_y - 13)])
        pygame.draw.polygon(surf, (86, 56, 34),
                            [(hx + 2, body_y - 13), (hx + 6, body_y - 19), (hx + 8, body_y - 11)])
        pygame.draw.circle(surf, (200, 40, 40), (int(hx + e.facing * 3), int(body_y - 10)), 2)
        for i in range(3):
            tx = x - e.radius + i * 6
            ty = body_y + 10 + math.sin(e.wobble * 1.4 + i) * 2
            pygame.draw.line(surf, col, (tx, body_y + 6), (tx - 3, ty), 3)

    # health bar
    if e.hp < e.max_hp:
        w = 30
        frac = max(0.0, e.hp / e.max_hp)
        pygame.draw.rect(surf, (20, 16, 22), (x - w // 2, y - e.radius - 14, w, 5))
        pygame.draw.rect(surf, (206, 66, 66),
                         (x - w // 2, y - e.radius - 14, int(w * frac), 5))


def draw_player(surf, p, cam_x, cam_y, t):
    x = p.x - cam_x
    y = p.y - cam_y
    bob = math.sin(p.bob) * 1.6
    flash = p.hurt_flash > 0

    # shadow
    pygame.draw.ellipse(surf, (0, 0, 0, 70), (x - 12, y + 6, 24, 9))

    skin = (240, 200, 158) if not flash else (255, 170, 170)
    shirt = (232, 232, 236) if not flash else (255, 190, 190)
    hair = (58, 40, 28)

    # legs
    pygame.draw.rect(surf, (54, 46, 62), (x - 8, y - 4 + bob, 7, 14))
    pygame.draw.rect(surf, (54, 46, 62), (x + 1, y - 4 - bob, 7, 14))
    pygame.draw.rect(surf, (36, 30, 42), (x - 9, y + 8 + bob, 9, 5))
    pygame.draw.rect(surf, (36, 30, 42), (x, y + 8 - bob, 9, 5))

    # body
    pygame.draw.rect(surf, shirt, (x - 9, y - 20 + bob * 0.5, 18, 18))
    pygame.draw.rect(surf, (196, 196, 202), (x - 9, y - 20 + bob * 0.5, 18, 18), 1)

    # arm / swing
    swing_angle = 0.0
    if p.swing > 0:
        swing_angle = math.sin((1.0 - p.swing) * math.pi) * 1.2
    ax = x + p.facing * (12 + math.sin(swing_angle) * 8)
    ay = y - 14 + bob * 0.5 + math.cos(swing_angle) * 4
    pygame.draw.line(surf, skin, (x + p.facing * 6, y - 14 + bob * 0.5), (ax, ay), 5)

    # head
    pygame.draw.circle(surf, skin, (int(x), int(y - 30 + bob * 0.5)), 11)
    # hair
    pygame.draw.circle(surf, hair, (int(x), int(y - 33 + bob * 0.5)), 11)
    pygame.draw.rect(surf, skin, (x - 11, y - 30 + bob * 0.5, 22, 10))
    pygame.draw.circle(surf, hair, (int(x - 10), int(y - 34 + bob * 0.5)), 6)
    pygame.draw.circle(surf, hair, (int(x + 10), int(y - 34 + bob * 0.5)), 6)
    pygame.draw.circle(surf, hair, (int(x), int(y - 41 + bob * 0.5)), 8)
    # eyes
    ex = x + p.facing * 3
    pygame.draw.circle(surf, (250, 250, 250), (int(ex - 3), int(y - 30 + bob * 0.5)), 3)
    pygame.draw.circle(surf, (250, 250, 250), (int(ex + 4), int(y - 30 + bob * 0.5)), 3)
    pygame.draw.circle(surf, (32, 28, 36), (int(ex - 2 + p.facing), int(y - 30 + bob * 0.5)), 1)
    pygame.draw.circle(surf, (32, 28, 36), (int(ex + 5 + p.facing), int(y - 30 + bob * 0.5)), 1)

    # torch flame
    if p.torch_on:
        fx = x + p.facing * 16
        fy = y - 26 + bob * 0.5
        flick = math.sin(t * 22) * 2
        pygame.draw.circle(surf, (255, 176, 60), (int(fx), int(fy + flick)), 6)
        pygame.draw.circle(surf, (255, 236, 150), (int(fx), int(fy + flick)), 3)


# ==========================================================================
# HUD
# ==========================================================================
def draw_stat_orb(surf, cx, cy, radius, frac, fill_color, rim_color, label, font):
    pygame.draw.circle(surf, (28, 24, 32), (cx, cy), radius)
    pygame.draw.circle(surf, (60, 54, 66), (cx, cy), radius, 2)
    if frac > 0:
        r = int(radius * max(0.0, min(1.0, frac)))
        if r > 0:
            pygame.draw.circle(surf, fill_color, (cx, cy + (radius - r)), r)
    pygame.draw.circle(surf, rim_color, (cx, cy), radius, 3)
    txt = font.render(label, True, (240, 240, 248))
    surf.blit(txt, (cx - txt.get_width() // 2, cy - txt.get_height() // 2))


def draw_hud(surf, player, world, day, clock_frac, fonts, show_craft):
    font, font_s, font_b = fonts

    # ---- inventory bar ----
    n = 10
    slot = 52
    pad = 6
    total = n * (slot + pad) - pad
    sx = (SCREEN_W - total) // 2
    sy = SCREEN_H - slot - 16

    panel = pygame.Surface((total + 16, slot + 16), pygame.SRCALPHA)
    panel.fill((16, 14, 20, 175))
    surf.blit(panel, (sx - 8, sy - 8))

    for i in range(n):
        x = sx + i * (slot + pad)
        sel = (i == player.selected and i < len(player.slots))
        pygame.draw.rect(surf, (70, 64, 78), (x, sy, slot, slot), 2)
        if sel:
            pygame.draw.rect(surf, (238, 206, 108), (x - 2, sy - 2, slot + 4, slot + 4), 3)
        if i < len(player.slots):
            name = player.slots[i]
            info = ITEMS[name]
            col = info['color']
            # item swatch
            pygame.draw.rect(surf, col, (x + 10, sy + 10, slot - 20, slot - 20))
            pygame.draw.rect(surf, (24, 20, 28), (x + 10, sy + 10, slot - 20, slot - 20), 2)
            if name == 'torch' and player.torch_on:
                pygame.draw.circle(surf, (255, 210, 90), (x + slot // 2, sy + slot // 2), 8, 2)
            cnt = player.inv.get(name, 0)
            if cnt > 1:
                t = font_s.render(str(cnt), True, (255, 255, 255))
                surf.blit(t, (x + slot - t.get_width() - 5, sy + slot - t.get_height() - 3))
        key = font_s.render(str((i + 1) % 10), True, (140, 136, 150))
        surf.blit(key, (x + 4, sy + 2))

    sel_name = player.selected_item()
    if sel_name:
        label = font_b.render(ITEMS[sel_name]['name'], True, (246, 244, 250))
        surf.blit(label, (SCREEN_W // 2 - label.get_width() // 2, sy - 34))

    # ---- stat orbs ----
    ox = SCREEN_W - 190
    oy = SCREEN_H - 60
    draw_stat_orb(surf, ox, oy, 30, player.health / player.max_health,
                  (206, 62, 66), (238, 120, 120), "HP", font_s)
    draw_stat_orb(surf, ox + 72, oy, 30, player.hunger / player.max_hunger,
                  (198, 138, 58), (238, 186, 110), "FOOD", font_s)
    draw_stat_orb(surf, ox + 144, oy, 30, player.sanity / player.max_sanity,
                  (128, 96, 200), (176, 150, 236), "SAN", font_s)

    # ---- day / clock ----
    txt = font_b.render(f"Day {day}", True, (250, 246, 226))
    surf.blit(txt, (SCREEN_W - txt.get_width() - 22, 16))

    cx, cy, r = SCREEN_W - 52, 78, 26
    pygame.draw.circle(surf, (22, 20, 28), (cx, cy), r)
    if clock_frac < DAY_LEN / CYCLE_LEN:
        phase_col = (250, 214, 110)
    elif clock_frac < (DAY_LEN + DUSK_LEN) / CYCLE_LEN:
        phase_col = (226, 138, 76)
    else:
        phase_col = (70, 78, 140)
    pygame.draw.circle(surf, phase_col, (cx, cy), r - 3)
    pygame.draw.circle(surf, (44, 40, 52), (cx, cy), r, 3)

    # ---- prompts ----
    tips = "WASD move | LMB chop/mine/attack | RMB use/place | C craft | 1-0 slots"
    t = font_s.render(tips, True, (216, 214, 226))
    surf.blit(t, (16, SCREEN_H - 26))

    # ---- crafting ----
    if show_craft:
        panel_w = 320
        panel_h = 60 + len(RECIPES) * 46
        px, py = 18, 70
        p = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        p.fill((18, 16, 24, 225))
        surf.blit(p, (px, py))
        pygame.draw.rect(surf, (92, 84, 104), (px, py, panel_w, panel_h), 2)
        hdr = font_b.render("CRAFTING", True, (246, 220, 140))
        surf.blit(hdr, (px + 14, py + 12))

        mx, my = pygame.mouse.get_pos()
        hover_idx = -1
        for i, (name, ing) in enumerate(RECIPES):
            ry = py + 46 + i * 46
            rect = pygame.Rect(px + 10, ry, panel_w - 20, 40)
            if rect.collidepoint(mx, my):
                hover_idx = i
                pygame.draw.rect(surf, (48, 42, 58), rect, border_radius=5)
            info = ITEMS[name]
            if name in player.tools:
                ok = True
                label_col = (150, 220, 150)
            else:
                ok = player.can_craft(ing)
                label_col = (238, 236, 246) if ok else (128, 124, 136)
            pygame.draw.rect(surf, info['color'], (rect.x + 4, rect.y + 6, 28, 28))
            pygame.draw.rect(surf, (20, 18, 24), (rect.x + 4, rect.y + 6, 28, 28), 2)
            nm = font.render(info['name'], True, label_col)
            surf.blit(nm, (rect.x + 40, rect.y + 3))
            parts = "  ".join(f"{v}x {ITEMS[k]['name']}" for k, v in ing.items())
            sub = font_s.render(parts, True,
                                (188, 184, 196) if ok else (140, 100, 100))
            surf.blit(sub, (rect.x + 40, rect.y + 21))
            if name in player.tools:
                own = font_s.render("OWNED", True, (140, 220, 140))
                surf.blit(own, (rect.right - own.get_width() - 8, rect.y + 12))

        return hover_idx
    return -1


# ==========================================================================
# Main
# ==========================================================================
def main():
    pygame.init()
    pygame.display.set_caption("PyStarve")
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    clock = pygame.time.Clock()

    font = pygame.font.SysFont("consolas", 15)
    font_s = pygame.font.SysFont("consolas", 12)
    font_b = pygame.font.SysFont("consolas", 20, bold=True)
    font_big = pygame.font.SysFont("consolas", 44, bold=True)
    fonts = (font, font_s, font_b)

    print("Generating world...")
    seed = random.randrange(1, 10 ** 9)
    world = World(seed)
    ground_tex = make_ground_textures()
    print(f"World ready (seed {seed}), {len(world.resources)} resources.")

    def new_game():
        sx = (WORLD_W // 2) * TILE
        sy = (WORLD_H // 2) * TILE
        return Player(sx, sy)

    player = new_game()
    player.add_item('twigs', 4)
    player.add_item('grass', 6)
    player.add_item('flint', 2)

    darkness = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
    light_layer = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)

    day_count = 1
    cycle_t = 0.0
    spawn_timer = 6.0
    spider_timer = 14.0
    show_craft = False
    time_acc = 0.0
    death_timer = 0.0

    def daylight_factor(t):
        if t < DAY_LEN:
            return 1.0
        if t < DAY_LEN + DUSK_LEN:
            return 1.0 - (t - DAY_LEN) / DUSK_LEN
        return 0.0

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        dt = min(dt, 0.05)
        time_acc += dt

        # ------------------------------------------------------------------
        # Events
        # ------------------------------------------------------------------
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_c and not player.dead:
                    show_craft = not show_craft
                elif event.key == pygame.K_r and player.dead:
                    world = World(random.randrange(1, 10 ** 9))
                    ground_tex = make_ground_textures()
                    player = new_game()
                    player.add_item('twigs', 4)
                    player.add_item('grass', 6)
                    player.add_item('flint', 2)
                    day_count = 1
                    cycle_t = 0.0
                    death_timer = 0.0
                    show_craft = False
                elif pygame.K_1 <= event.key <= pygame.K_9:
                    player.selected = event.key - pygame.K_1
                elif event.key == pygame.K_0:
                    player.selected = 9
                elif event.key == pygame.K_q:
                    if player.slots:
                        player.selected = (player.selected - 1) % len(player.slots)
                elif event.key == pygame.K_e:
                    if player.slots:
                        player.selected = (player.selected + 1) % len(player.slots)
            elif event.type == pygame.MOUSEWHEEL:
                if player.slots:
                    player.selected = (player.selected - event.y) % len(player.slots)

        keys = pygame.key.get_pressed()
        mbuttons = pygame.mouse.get_pressed()

        # ------------------------------------------------------------------
        # Update world time
        # ------------------------------------------------------------------
        if not player.dead:
            cycle_t += dt
            if cycle_t >= CYCLE_LEN:
                cycle_t -= CYCLE_LEN
                day_count += 1

        dl = daylight_factor(cycle_t)
        is_night = dl < 0.05

        # ------------------------------------------------------------------
        # Player
        # ------------------------------------------------------------------
        if not player.dead:
            player.update(dt, keys, world)

        # ------------------------------------------------------------------
        # Camera
        # ------------------------------------------------------------------
        cam_x = player.x - SCREEN_W / 2
        cam_y = player.y - SCREEN_H / 2
        cam_x = max(0, min(WORLD_W * TILE - SCREEN_W, cam_x))
        cam_y = max(0, min(WORLD_H * TILE - SCREEN_H, cam_y))
        cam_x, cam_y = int(cam_x), int(cam_y)

        # ------------------------------------------------------------------
        # Aiming / targeting
        # ------------------------------------------------------------------
        mx, my = pygame.mouse.get_pos()
        wmx = mx + cam_x
        wmy = my + cam_y

        target_res = None
        target_enemy = None
        best_d = 1e9
        for res in world.resources:
            if res.respawn_t > 0:
                continue
            dx = wmx - res.x
            dy = wmy - res.y
            d = math.hypot(dx, dy)
            if d < res.radius + 12 and d < best_d:
                pdx = res.x - player.x
                pdy = res.y - player.y
                if math.hypot(pdx, pdy) <= REACH + res.radius:
                    best_d = d
                    target_res = res
                    target_enemy = None

        for e in world.enemies:
            if e.dead:
                continue
            dx = wmx - e.x
            dy = wmy - e.y
            d = math.hypot(dx, dy)
            if d < e.radius + 14 and d < best_d:
                pdx = e.x - player.x
                pdy = e.y - player.y
                if math.hypot(pdx, pdy) <= REACH + e.radius:
                    best_d = d
                    target_enemy = e
                    target_res = None

        # ------------------------------------------------------------------
        # Left click: work / attack
        # ------------------------------------------------------------------
        want_action = (mbuttons[0] or keys[pygame.K_SPACE]) and not player.dead and not show_craft

        if want_action and player.action_cd <= 0:
            if target_enemy is not None:
                dmg = 12.0
                if 'spear' in player.tools:
                    dmg = 30.0
                elif 'axe' in player.tools:
                    dmg = 18.0
                target_enemy.hp -= dmg
                target_enemy.hit_flash = 0.16
                player.action_cd = ACTION_CD
                player.swing = 1.0
                knock = 16.0
                kd = math.hypot(target_enemy.x - player.x, target_enemy.y - player.y) or 1
                target_enemy.x += (target_enemy.x - player.x) / kd * knock
                target_enemy.y += (target_enemy.y - player.y) / kd * knock
                world.add_particles(target_enemy.x, target_enemy.y, (200, 60, 60), 6)
                if target_enemy.hp <= 0:
                    target_enemy.dead = True
                    for item, lo, hi in target_enemy.drops:
                        n = random.randint(lo, hi)
                        if player.add_item(item, n):
                            pass
                    world.add_particles(target_enemy.x, target_enemy.y, (140, 60, 60), 14)

            elif target_res is not None:
                work = target_res.work
                dmg = 1.0
                if work == 'chop' and 'axe' in player.tools:
                    dmg = 2.5
                elif work == 'chop' and 'spear' in player.tools:
                    dmg = 1.5
                elif work == 'mine' and 'pickaxe' in player.tools:
                    dmg = 2.5
                elif work == 'mine':
                    dmg = 0.5
                destroyed = target_res.damage(dmg)
                player.action_cd = ACTION_CD
                player.swing = 1.0
                world.add_particles(target_res.x, target_res.y - 8,
                                    (150, 120, 90), 5)
                if destroyed:
                    for item, lo, hi in target_res.drops:
                        n = random.randint(lo, hi)
                        player.add_item(item, n)
                    world.add_particles(target_res.x, target_res.y, (170, 150, 110), 12)

        # ------------------------------------------------------------------
        # Right click: use / place selected
        # ------------------------------------------------------------------
        if mbuttons[2] and not player.dead and not show_craft:
            if player.action_cd <= 0:
                sel = player.selected_item()
                if sel == 'torch':
                    player.torch_on = not player.torch_on
                    player.torch_time = 9999.0
                    player.action_cd = 0.25
                elif sel == 'campfire':
                    px = wmx
                    py = wmy
                    dx = px - player.x
                    dy = py - player.y
                    if math.hypot(dx, dy) <= REACH:
                        if player.remove_item('campfire', 1):
                            world.campfires.append(Campfire(px, py))
                            player.action_cd = 0.3
                elif sel and sel in ITEMS and ('food' in ITEMS[sel] or 'sanity' in ITEMS[sel]):
                    if player.eat(sel):
                        player.action_cd = 0.4
                        world.add_particles(player.x, player.y - 20, (220, 180, 120), 6)

        # ------------------------------------------------------------------
        # Update entities
        # ------------------------------------------------------------------
        alive = []
        for res in world.resources:
            res.update(dt)
            if not res.dead:
                alive.append(res)
        world.resources = alive

        for e in world.enemies:
            if e.dead:
                continue
            e.update(dt, player, world)
        world.enemies = [e for e in world.enemies if not e.dead]

        # campfires
        for cf in world.campfires:
            cf.update(dt, world)
        world.campfires = [c for c in world.campfires if not c.dead]

        # ------------------------------------------------------------------
        # Light sources
        # ------------------------------------------------------------------
        world.lights = []
        for cf in world.campfires:
            if cf.fuel > 0:
                world.lights.append((cf.x, cf.y, cf.radius, cf.intensity))
        if player.torch_on:
            world.lights.append((player.x, player.y, 150, 1.0))

        # ------------------------------------------------------------------
        # Darkness damage / sanity
        # ------------------------------------------------------------------
        if not player.dead:
            light_here = dl
            for (lx, ly, lr, li) in world.lights:
                d = math.hypot(player.x - lx, player.y - ly)
                if d < lr:
                    light_here = max(light_here, li * (1.0 - d / lr))
            if light_here < 0.14:
                player.take_damage(9.0 * dt)
                if random.random() < dt * 3:
                    world.add_particles(player.x, player.y - 24, (120, 60, 160), 2)

            # sanity
            if dl > 0.7:
                player.sanity = min(player.max_sanity, player.sanity + 3.2 * dt)
            elif is_night and light_here < 0.35:
                player.sanity -= 2.6 * dt
            elif light_here > 0.5:
                player.sanity = min(player.max_sanity, player.sanity + 1.2 * dt)
            for e in world.enemies:
                if math.hypot(e.x - player.x, e.y - player.y) < 170:
                    player.sanity -= 1.6 * dt
            player.sanity = max(0.0, player.sanity)
            if player.sanity <= 0:
                player.take_damage(2.0 * dt)

        # ------------------------------------------------------------------
        # Enemy spawning
        # ------------------------------------------------------------------
        if not player.dead:
            if is_night:
                spawn_timer -= dt
                if spawn_timer <= 0 and len(world.enemies) < 12:
                    world.spawn_hound(player.x, player.y)
                    spawn_timer = random.uniform(7.0, 12.0)
                spider_timer -= dt
                if spider_timer <= 0 and len(world.enemies) < 14:
                    world.spawn_spider(player.x, player.y)
                    spider_timer = random.uniform(11.0, 18.0)
            else:
                spawn_timer = max(spawn_timer, 4.0)
                spider_timer = max(spider_timer, 9.0)
                # hounds burn in daylight
                for e in world.enemies:
                    if e.kind == 'hound':
                        e.hp -= 8.0 * dt
                        if e.hp <= 0:
                            e.dead = True
                world.enemies = [e for e in world.enemies if not e.dead]

        # ------------------------------------------------------------------
        # Particles
        # ------------------------------------------------------------------
        alive_p = []
        for p in world.particles:
            p[3] += 0.28
            p[0] += p[2]
            p[1] += p[3]
            p[4] -= dt
            if p[4] > 0:
                alive_p.append(p)
        world.particles = alive_p

        # ------------------------------------------------------------------
        # RENDER
        # ------------------------------------------------------------------
        # ground
        tx0 = max(0, cam_x // TILE)
        ty0 = max(0, cam_y // TILE)
        tx1 = min(WORLD_W, tx0 + SCREEN_W // TILE + 2)
        ty1 = min(WORLD_H, ty0 + SCREEN_H // TILE + 2)

        for ty in range(ty0, ty1):
            sy_px = ty * TILE - cam_y
            row = ty * WORLD_W
            for tx in range(tx0, tx1):
                g = world.ground[row + tx]
                variant = (tx * 7 + ty * 13) & 3
                screen.blit(ground_tex[g][variant], (tx * TILE - cam_x, sy_px))

        # entities sorted by y for depth
        drawables = []
        for res in world.resources:
            if -60 < res.x - cam_x < SCREEN_W + 60 and -120 < res.y - cam_y < SCREEN_H + 120:
                drawables.append((res.y, 'res', res))
        for e in world.enemies:
            if -60 < e.x - cam_x < SCREEN_W + 60 and -60 < e.y - cam_y < SCREEN_H + 60:
                drawables.append((e.y, 'enemy', e))
        for cf in world.campfires:
            drawables.append((cf.y, 'fire', cf))
        drawables.append((player.y, 'player', player))
        drawables.sort(key=lambda d: d[0])

        for _, kind, obj in drawables:
            if kind == 'res':
                draw_resource(screen, obj, cam_x, cam_y, time_acc)
            elif kind == 'enemy':
                draw_enemy(screen, obj, cam_x, cam_y, time_acc)
            elif kind == 'fire':
                obj.draw(screen, cam_x, cam_y, time_acc)
            else:
                draw_player(screen, obj, cam_x, cam_y, time_acc)

        # particles
        for p in world.particles:
            a = max(0, min(255, int(255 * (p[4] / 0.65))))
            c = p[5]
            s = pygame.Surface((4, 4), pygame.SRCALPHA)
            s.fill((c[0], c[1], c[2], a))
            screen.blit(s, (int(p[0] - cam_x), int(p[1] - cam_y)))

        # target highlight
        if target_res is not None:
            hx = target_res.x - cam_x
            hy = target_res.y - cam_y
            pygame.draw.circle(screen, (255, 255, 255), (int(hx), int(hy)),
                               target_res.radius + 4, 2)
        if target_enemy is not None:
            hx = target_enemy.x - cam_x
            hy = target_enemy.y - cam_y
            pygame.draw.circle(screen, (255, 110, 110), (int(hx), int(hy)),
                               target_enemy.radius + 5, 2)

        # ---- darkness ----
        night_alpha = int(235 * (1.0 - dl))
        if night_alpha > 4 or world.lights:
            darkness.fill((0, 0, 0, night_alpha))
            light_layer.fill((0, 0, 0, 0))
            for (lx, ly, lr, li) in world.lights:
                sx_ = int(lx - cam_x - lr)
                sy_ = int(ly - cam_y - lr)
                blob = light_blob(int(lr * 2))
                light_layer.blit(blob, (sx_, sy_))
            darkness.blit(light_layer, (0, 0), special_flags=pygame.BLEND_RGBA_SUB)
            screen.blit(darkness, (0, 0))

        # vignette when sanity low
        if player.sanity < 40 and not player.dead:
            k = 1.0 - player.sanity / 40.0
            pulse = 0.5 + 0.5 * math.sin(time_acc * 3.0)
            a = int(90 * k * pulse)
            vig = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            vig.fill((60, 0, 80, a))
            screen.blit(vig, (0, 0))

        # ---- HUD ----
        hover = draw_hud(screen, player, world, day_count,
                         cycle_t / CYCLE_LEN, fonts, show_craft)

        # crafting click handling
        if show_craft and hover >= 0 and not player.dead:
            if pygame.mouse.get_pressed()[0]:
                name = RECIPES[hover][0]
                player.craft(name)

        # hurt flash
        if player.hurt_flash > 0:
            fl = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            fl.fill((190, 20, 20, int(90 * player.hurt_flash / 0.30)))
            screen.blit(fl, (0, 0))

        # ---- death screen ----
        if player.dead:
            death_timer += dt
            ov = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ov.fill((8, 6, 12, min(215, int(death_timer * 300))))
            screen.blit(ov, (0, 0))
            if death_timer > 0.4:
                msg = "YOU DIED"
                t1 = font_big.render(msg, True, (222, 62, 62))
                screen.blit(t1, (SCREEN_W // 2 - t1.get_width() // 2, SCREEN_H // 2 - 90))
                sub = font_b.render(f"You survived {day_count} day"
                                    f"{'s' if day_count != 1 else ''}.",
                                    True, (238, 232, 216))
                screen.blit(sub, (SCREEN_W // 2 - sub.get_width() // 2, SCREEN_H // 2 - 20))
                hint = font.render("Press  R  to start a new world",
                                   True, (200, 196, 210))
                screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, SCREEN_H // 2 + 30))

        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


# ==========================================================================
# Campfire
# ==========================================================================
class Campfire:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.fuel = 55.0
        self.max_fuel = 55.0
        self.radius = 190.0
        self.intensity = 1.0
        self.dead = False
        self.flicker = random.uniform(0, math.tau)

    def update(self, dt, world):
        self.fuel -= dt
        self.flicker += dt * 12.0
        if self.fuel <= 0:
            self.dead = True
            world.add_particles(self.x, self.y, (90, 90, 96), 10)
            return
        self.intensity = 0.55 + 0.45 * min(1.0, self.fuel / 12.0)
        self.radius = 120.0 + 90.0 * min(1.0, self.fuel / 25.0)

        if random.random() < dt * 26:
            world.particles.append([
                self.x + random.uniform(-6, 6), self.y - 4,
                random.uniform(-0.5, 0.5), random.uniform(-1.9, -0.7),
                random.uniform(0.3, 0.7),
                random.choice([(255, 196, 80), (255, 148, 52), (255, 236, 160)]),
            ])

    def draw(self, surf, cam_x, cam_y, t):
        x = self.x - cam_x
        y = self.y - cam_y
        if x < -80 or x > SCREEN_W + 80 or y < -80 or y > SCREEN_H + 80:
            return

        # warm ground glow
        glow = pygame.Surface((90, 60), pygame.SRCALPHA)
        pygame.draw.ellipse(glow, (255, 160, 60, 40), (0, 0, 90, 60))
        surf.blit(glow, (x - 45, y - 20))

        # stones
        for i in range(6):
            a = i * math.tau / 6
            sx = x + math.cos(a) * 16
            sy = y + math.sin(a) * 8 + 4
            pygame.draw.circle(surf, (108, 104, 108), (int(sx), int(sy)), 5)

        # logs
        pygame.draw.line(surf, (92, 60, 34), (x - 12, y + 4), (x + 12, y - 2), 7)
        pygame.draw.line(surf, (76, 50, 28), (x - 10, y - 4), (x + 10, y + 6), 7)

        # flames
        fl = math.sin(self.flicker) * 2.0
        h = 12 + 8 * min(1.0, self.fuel / 20.0)
        pygame.draw.polygon(surf, (232, 110, 40),
                            [(x - 9, y - 2), (x, y - h - 10 + fl), (x + 9, y - 2)])
        pygame.draw.polygon(surf, (255, 172, 60),
                            [(x - 6, y - 3), (x + 1, y - h - 4 + fl), (x + 6, y - 3)])
        pygame.draw.polygon(surf, (255, 236, 170),
                            [(x - 3, y - 4), (x, y - h + 2 + fl * 0.6), (x + 3, y - 4)])

if __name__ == "__main__":
    main()