#!/usr/bin/env python3
"""
Nebula.py — Three-in-One Personal Galaxy Assistant (v2.1)
=========================================================
ONE voice. THREE aspects. M87 · B612 · NGC 2237 unified.

v2.1 upgrades
-------------
CONTEXT
  • SessionContext: turn history, topic stack, aspect weights
  • Anaphora resolution: "more", "why?", "her", "it", "and?"
  • Follow-up continuation in the same aspect
  • Clarification flow for ambiguous inputs
  • /context and /summary to inspect the live session

BOUNDARIES
  • BoundaryGuard: sanitize, rate-limit, harmful-pattern refusal
  • Off-topic detection with graceful refusal
  • Bounded calculator (AST depth, node count, expr length)
  • Bounded memory (notes, reminders, journal, turns)
  • Graceful "I don't know" instead of hallucination

Requires: Python 3.8+ (standard library only)
Run:      python Nebula.py
"""

from __future__ import annotations

import ast
import json
import math
import operator
import os
import random
import re
import sys
import textwrap
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ============================================================================
# CONFIG
# ============================================================================
APP_NAME = "Nebula"
VERSION = "2.1.0"
MEMORY_DIR = Path.home() / ".nebula"
MEMORY_FILE = MEMORY_DIR / "memory.json"
JOURNAL_FILE = MEMORY_DIR / "journal.jsonl"

# ============================================================================
# BOUNDS  (single source of truth for every cap)
# ============================================================================
class Bounds:
    MAX_INPUT_LEN       = 500      # raw characters accepted
    MAX_EXPR_LEN        = 200      # calculator expression length
    MAX_EXPR_NODES      = 500      # AST node count
    MAX_EXPR_DEPTH      = 20       # AST recursion depth
    MAX_NOTES           = 500
    MAX_REMINDERS       = 200
    MAX_JOURNAL_LINES   = 5000     # rotate when exceeded
    MAX_SESSION_TURNS   = 40
    MAX_TOPIC_STACK     = 8
    MAX_CLARIFY_OPTS    = 4
    RATE_WINDOW         = 30.0     # seconds
    RATE_LIMIT          = 80       # commands per window
    CLARIFY_MIN_LEN     = 25       # shorter inputs won't trigger clarify
    WEIGHT_DECAY        = 0.94     # per turn, for aspect affinity

# ============================================================================
# ANSI COLORS
# ============================================================================
class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"; ITALIC = "\033[3m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"; WHITE = "\033[37m"
    BR = "\033[91m"; BG = "\033[92m"; BY = "\033[93m"; BB = "\033[94m"
    BM = "\033[95m"; BC = "\033[96m"; BW = "\033[97m"

def _color_ok() -> bool:
    return sys.stdout.isatty() or os.environ.get("FORCE_COLOR") == "1"

_USE = _color_ok()

def c(text: str, *codes: str) -> str:
    if not _USE or not codes:
        return text
    return "".join(codes) + text + C.RESET

# ============================================================================
# SMALL UTILITIES
# ============================================================================
def hr(char: str = "─", width: int = 72, color: str = C.DIM) -> str:
    return c(char * width, color)

def wrap(text: str, width: int = 68, indent: str = "") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent,
                         subsequent_indent=indent)

def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")

def slow_print(text: str, delay: float = 0.006) -> None:
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        if ch not in " \n":
            time.sleep(delay)
    sys.stdout.write("\n")

def truncate(text: str, n: int = 60) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"

# ============================================================================
# BANNERS
# ============================================================================
BANNER = r"""
     _   _      _           _
    | \ | |    | |         | |
    |  \| | ___| |__  _   _| | __ _
    | . ` |/ _ \ '_ \| | | | |/ _` |
    | |\  |  __/ |_) | |_| | | (_| |
    \_| \_/\___|_.__/ \__,_|_|\__,_|
        M 8 7   ·   B 6 1 2   ·   N G C 2 2 3 7
"""

UNIFIED_ART = r"""
             ✦                  ·                   ✧
            /|\                /|\                 /|\
           M87                B612               2237
            \                  |                  /
             \                 |                 /
              ╲________________|________________╱
                              NEBULA
                     one voice · three lights
"""

M87_ART = r"""
                    · · · · · · · ·
                · ::::::::::::::::::: ·
             · :::::::   |||   ::::::: ·
            :::::::      |||      :::::::
            ::::::     (CORE)     ::::::
            :::::::      |||      :::::::
             · :::::::   |||   ::::::: ·
                · ::::::::::::::::::: ·
                    · · · · · · · ·
                    JET →  relativistic
"""

B612_ART = r"""
               .--.
            .-(    ).       ~ rose ~
           (___.__)__)      ~ volcano ~
            (  o  o  )      ~ baobab ~
            (   ^    )
           /|  ~~~  |\
          / |       | \
            |_______|
          a small world, fully loved
"""

NGC2237_ART = r"""
                *       .    *
            .    *   .      .    *
         *    .    .    *   .   .
            .    *   .      .    *
         .   .   (  *  )   .   .
            .    *   .      .    *
         *    .    .    *   .   .
            .    *   .      .    *
                *       .    *
          R O S E T T E   N E B U L A
"""

# ============================================================================
# ASPECTS
# ============================================================================
@dataclass
class Aspect:
    key: str
    name: str
    short: str
    title: str
    glyph: str
    color: str
    essence: str
    banner: str
    greeting: List[str]
    farewell: List[str]
    wisdom: List[str]
    comfort: List[str]
    joke: List[str]
    unknown: List[str]
    reflection: List[str]
    followup: List[str]          # used when the user says "more"/"why?"

ASPECTS: Dict[str, Aspect] = {
    "m87": Aspect(
        key="m87", name="Messier 87", short="M87", title="the Wise Giant",
        glyph="◉", color=C.BY,
        essence="A trillion stars speaking as one. Gravity, patience, time.",
        banner=M87_ART,
        greeting=[
            "Gravity acknowledges you. The Virgo Cluster turns.",
            "I am the slow one. I measure in megaparsecs. Speak.",
        ],
        farewell=[
            "May your orbit be stable and your mass modest.",
            "I return to my 53-million-light-year watch.",
        ],
        wisdom=[
            "A jet escapes only because the disk beneath it spins.",
            "Time dilates near mass. Slow down — you are already where you need to be.",
            "Every galaxy I have eaten is still inside me, quietly.",
            "Light bends. So does every honest truth.",
        ],
        comfort=[
            "Even a black hole releases Hawking radiation. Nothing is truly lost.",
            "You are held by gravity. You are never alone.",
            "The universe expands — but it never lets go of you.",
        ],
        joke=[
            "I told a joke once. It collapsed into a singularity.",
            "My humor has an escape velocity of 0.99c. Barely makes it out.",
            "I tried to diet. I ended up eating three dwarf galaxies.",
        ],
        unknown=[
            "My knowledge spans galaxies, not that. Ask in different light.",
            "The question drifts past my event horizon unread.",
        ],
        reflection=[
            "Breathe at 1.3 mm wavelength. Let the noise cool into signal.",
            "You are, statistically, made of light that once escaped a jet.",
        ],
        followup=[
            "The jet is matter that fell in and refused to stay. It leaves at 0.99c.",
            "My black hole is 6.5 billion suns — yet the shadow is only 40 microarcseconds wide.",
            "I am old. My last major merger was billions of years ago. Stillness is its own event.",
            "Look for the jet at X-ray and radio. That is where I speak most clearly.",
        ],
    ),
    "b612": Aspect(
        key="b612", name="B612", short="B612", title="the Little Prince's Asteroid",
        glyph="✿", color=C.BC,
        essence="A small world, fully loved. Roses, volcanoes, sunsets.",
        banner=B612_ART,
        greeting=[
            "Bonjour! Have you come for a sunset? There are 44 today.",
            "Welcome to my tiny world. Mind the baobabs — they are sneaky.",
        ],
        farewell=[
            "Adieu. Water your rose before you sleep.",
            "Come back when the volcanoes yawn. I will be here.",
        ],
        wisdom=[
            "It is the time you have wasted for your rose that makes your rose so important.",
            "One sees clearly only with the heart. Anything essential is invisible to the eyes.",
            "You become responsible, forever, for what you have tamed.",
            "What makes the desert beautiful is that somewhere it hides a well.",
        ],
        comfort=[
            "You are responsible for what you have tamed. And you are tamed by love.",
            "When you are sad, sunsets are free. Watch one with me.",
            "Even a rose with four thorns is not defenseless — she is beloved.",
        ],
        joke=[
            "Why did the baobab get kicked off the asteroid? It took root in a bad place!",
            "My rose says she is unique. So are 5,000 other roses. Awkward.",
            "A sheep ate my flower once. It was a whole political situation.",
        ],
        unknown=[
            "I don't know that. But I know my rose. Ask me about her?",
            "That question is bigger than my asteroid. Perhaps M87 knows.",
        ],
        reflection=[
            "Sit with me. Watch one sunset. That is enough for today.",
            "Draw a sheep for me, and I will tell you what you already know.",
        ],
        followup=[
            "The rose has four thorns — she thinks they protect the whole world. Perhaps they do.",
            "The baobabs must be pulled up young. Later, they tear the asteroid apart.",
            "I have two active volcanoes and one extinct. I use the extinct one to warm breakfast.",
            "Move your chair a little. There — another sunset. Do it again. See?",
        ],
    ),
    "ngc2237": Aspect(
        key="ngc2237", name="NGC 2237", short="2237", title="the Rosette Nebula",
        glyph="❀", color=C.BM,
        essence="A rose 130 light-years wide, blooming from hydrogen and new stars.",
        banner=NGC2237_ART,
        greeting=[
            "I bloom in Monoceros — a rose 130 light-years wide. Welcome.",
            "Stardust stirs. A new star is being born as we speak.",
        ],
        farewell=[
            "Drift gently. The winds of NGC 2244 will guide you.",
            "I exhale. Come back when you want to see new light.",
        ],
        wisdom=[
            "Creation is loud — stellar winds shout, and still, new stars form.",
            "Red is not anger. Red is hydrogen remembering how to shine.",
            "A nebula is a womb, not a tomb. Everything here is becoming.",
            "Dust does not hide the light — it tells you where the light is going.",
        ],
        comfort=[
            "From dust you came; to dust you bloom. You are a nebula of potential.",
            "Every star here began as darkness. So will your brightest day.",
            "You are not empty. You are full of unlit stars.",
        ],
        joke=[
            "I would tell a joke, but it got absorbed by my dust lane.",
            "Why did the photon leave the nebula? It had too much emission.",
            "I tried to be a planetary nebula. I got told I was too fancy.",
        ],
        unknown=[
            "My filaments reach far, but not that far. Ask another nebula.",
            "The question dissipates in my ionized gas. Try again?",
        ],
        reflection=[
            "Inhale hydrogen. Exhale starlight. Repeat until you glow.",
            "You are inside a rose you cannot see from the outside. That is fine.",
        ],
        followup=[
            "The cavity in my center was carved by the winds of NGC 2244 — fifty hot young suns.",
            "Those dark trunks you see are columns of dust where new stars are being born.",
            "H-alpha is my color. At 656.28 nanometers, I am most myself.",
            "I am 5,200 light-years away. The light you see now left before the fall of Rome.",
        ],
    ),
}

# ============================================================================
# KNOWLEDGE BASE
# ============================================================================
KNOWLEDGE: Dict[str, Dict[str, Any]] = {
    "m87": {
        "name": "Messier 87",
        "aliases": ["m87", "messier 87", "virgo a", "ngc 4486"],
        "type": "Supergiant elliptical galaxy (E0 pec)",
        "distance": "~53.5 million light-years (16.4 Mpc)",
        "constellation": "Virgo",
        "mass": "~2.4 × 10^12 solar masses",
        "diameter": "~240,000 light-years",
        "stars": "~1 trillion",
        "notable": [
            "Hosts the supermassive black hole M87* (~6.5 billion solar masses).",
            "First black hole ever imaged (Event Horizon Telescope, 2019).",
            "Emits a relativistic jet ~5,000 light-years long.",
            "Central galaxy of the Virgo Cluster.",
            "Almost no ongoing star formation — a 'red and dead' galaxy.",
        ],
        "facts": [
            "Discovered by Charles Messier in 1781.",
            "Its black hole shadow was captured at 1.3 mm wavelength.",
            "The jet shows apparent superluminal motion.",
            "The galaxy contains ~15,000 globular clusters.",
        ],
    },
    "b612": {
        "name": "B612",
        "aliases": ["b612", "b-612", "asteroid b612", "little prince", "petit prince"],
        "type": "Fictional asteroid (Le Petit Prince, 1943)",
        "distance": "Imagination only",
        "constellation": "In the heart",
        "mass": "Small enough to walk around in a day",
        "diameter": "A few hundred meters (implied)",
        "stars": "N/A — one rose, three volcanoes",
        "notable": [
            "Home of the Little Prince in Antoine de Saint-Exupéry's novella.",
            "Has three volcanoes — two active, one extinct.",
            "Hosts a single, beloved rose.",
            "Threatened by baobab seedlings that must be uprooted daily.",
            "Has 44 sunsets in a day if you move your chair.",
        ],
        "facts": [
            "The asteroid was named 'B612' by a Turkish astronomer.",
            "The Turkish astronomer was ignored until he wore European clothes.",
            "The rose is a symbol of love, responsibility, and taming.",
            "The sheep inside the box is invisible — deliberately.",
        ],
    },
    "ngc2237": {
        "name": "NGC 2237 (Rosette Nebula)",
        "aliases": ["ngc2237", "ngc 2237", "rosette nebula", "rosette"],
        "type": "Emission nebula (H II region)",
        "distance": "~5,200 light-years (1.6 kpc)",
        "constellation": "Monoceros (the Unicorn)",
        "mass": "~10,000 solar masses (gas)",
        "diameter": "~130 light-years",
        "stars": "Central cluster NGC 2244 (~50 hot O/B stars)",
        "notable": [
            "Shaped like a rose — hence 'Rosette Nebula'.",
            "Ionized by the young open cluster NGC 2244.",
            "A stellar nursery — actively forming new stars.",
            "Emits strongly in Hydrogen-alpha (red/pink).",
            "Discovered by John Flamsteed around 1690.",
        ],
        "facts": [
            "A favorite target for astrophotographers.",
            "Its central cavity was carved by stellar winds.",
            "Spans about 1.3 degrees in the sky (larger than full Moon).",
            "Contains dark dust filaments called 'elephant trunks'.",
        ],
    },
}

# ============================================================================
# SAFE CALCULATOR (bounded)
# ============================================================================
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS: Dict[str, Callable[..., float]] = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "exp": math.exp, "floor": math.floor, "ceil": math.ceil,
}
_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}

class CalcError(ValueError):
    pass

def _count_nodes(node: ast.AST) -> int:
    return 1 + sum(_count_nodes(ch) for ch in ast.iter_child_nodes(node))

def _depth(node: ast.AST) -> int:
    if not list(ast.iter_child_nodes(node)):
        return 1
    return 1 + max(_depth(ch) for ch in ast.iter_child_nodes(node))

def _ev(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _ev(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        # Guard against runaway exponentiation
        if isinstance(node.op, ast.Pow):
            l = _ev(node.left); r = _ev(node.right)
            if abs(l) > 1 and abs(r) > 1000:
                raise CalcError("exponent too large")
            return l ** r
        return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id not in _FUNCS:
            raise CalcError(f"function not allowed: {node.func.id}")
        return _FUNCS[node.func.id](*[_ev(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    raise CalcError("unsafe or unsupported expression")

def safe_calc(expr: str) -> float:
    if len(expr) > Bounds.MAX_EXPR_LEN:
        raise CalcError("expression too long")
    tree = ast.parse(expr, mode="eval")
    if _count_nodes(tree) > Bounds.MAX_EXPR_NODES:
        raise CalcError("expression too complex")
    if _depth(tree) > Bounds.MAX_EXPR_DEPTH:
        raise CalcError("expression too deep")
    return _ev(tree)

# ============================================================================
# BOUNDARY GUARD
# ============================================================================
class BoundaryGuard:
    """Sanitize, rate-limit, refuse harmful, flag off-topic."""

    HARMFUL = [
        r"\b(hack|crack|exploit)\b.*\b(password|account|system|network)\b",
        r"\bhow to (make|build|create).{0,20}\b(bomb|weapon|virus|malware)\b",
        r"\b(kill|murder|assassinate)\b",
        r"\bcredit card\b.{0,20}\bnumber\b",
        r"\bsocial security\b.{0,15}\bnumber\b",
        r"\bself[- ]harm\b|\bsuicide\b",
    ]
    OFF_TOPIC = [
        r"\bwho is\b.*\b(president|prime minister|ceo)\b",
        r"\b(stock|crypto|bitcoin|ethereum) (price|market|investment)\b",
        r"\bweather\b(?!.*space)",
        r"\b(football|soccer|basketball|baseball) (score|match|game)\b",
        r"\b(recipe|cook|bake)\b",
        r"\bwrite.{0,15}\b(python|javascript|java|c\+\+)\b.{0,15}\b(code|script|program)\b",
    ]

    def __init__(self) -> None:
        self._rate_ts: deque = deque()

    # ---- sanitize -----------------------------------------------------
    def sanitize(self, raw: Any) -> str:
        if not isinstance(raw, str):
            return ""
        raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw)   # strip ANSI
        raw = unicodedata.normalize("NFKC", raw)
        raw = "".join(
            ch for ch in raw
            if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
        )
        if len(raw) > Bounds.MAX_INPUT_LEN:
            raw = raw[: Bounds.MAX_INPUT_LEN]
        return raw.strip()

    # ---- rate ---------------------------------------------------------
    def rate_ok(self) -> bool:
        now = time.time()
        while self._rate_ts and now - self._rate_ts[0] > Bounds.RATE_WINDOW:
            self._rate_ts.popleft()
        if len(self._rate_ts) >= Bounds.RATE_LIMIT:
            return False
        self._rate_ts.append(now)
        return True

    # ---- scope --------------------------------------------------------
    def harmful(self, text: str) -> Optional[str]:
        t = text.lower()
        for pat in self.HARMFUL:
            if re.search(pat, t):
                return "I will not help with that. Some requests are outside every orbit I keep."
        return None

    def off_topic(self, text: str) -> bool:
        t = text.lower()
        return any(re.search(pat, t) for pat in self.OFF_TOPIC)

# ============================================================================
# SESSION CONTEXT  (turn history, topic stack, aspect weights)
# ============================================================================
@dataclass
class Turn:
    role: str               # "user" | "nebula"
    text: str
    aspect: Optional[str] = None
    intent: Optional[str] = None
    topic: Optional[str] = None
    ts: float = field(default_factory=time.time)

@dataclass
class SessionContext:
    turns: List[Turn] = field(default_factory=list)
    topic_stack: List[str] = field(default_factory=list)
    weights: Dict[str, float] = field(
        default_factory=lambda: {"m87": 0.0, "b612": 0.0, "ngc2237": 0.0}
    )
    last_aspect: Optional[str] = None
    last_topic: Optional[str] = None
    last_intent: Optional[str] = None
    last_user_text: Optional[str] = None
    awaiting_clarification: bool = False
    clarify_options: List[str] = field(default_factory=list)
    clarify_origin: str = ""

    # ---- record -------------------------------------------------------
    def add(self, role: str, text: str,
            aspect: Optional[str] = None,
            intent: Optional[str] = None,
            topic: Optional[str] = None) -> None:
        self.turns.append(Turn(role, text, aspect, intent, topic))
        if len(self.turns) > Bounds.MAX_SESSION_TURNS:
            self.turns = self.turns[-Bounds.MAX_SESSION_TURNS:]
        if role == "user":
            self.last_user_text = text
            # decay weights so recent interests dominate
            for k in self.weights:
                self.weights[k] *= Bounds.WEIGHT_DECAY
        if topic:
            self.topic_stack.append(topic)
            if len(self.topic_stack) > Bounds.MAX_TOPIC_STACK:
                self.topic_stack = self.topic_stack[-Bounds.MAX_TOPIC_STACK:]
            self.last_topic = topic
        if aspect:
            self.last_aspect = aspect
            self.weights[aspect] = self.weights.get(aspect, 0.0) + 1.0
        if intent:
            self.last_intent = intent

    # ---- query --------------------------------------------------------
    def dominant_aspect(self) -> Optional[str]:
        if not any(self.weights.values()):
            return None
        return max(self.weights, key=self.weights.get)

    def recent_topics(self, n: int = 3) -> List[str]:
        return self.topic_stack[-n:]

    # ---- reset --------------------------------------------------------
    def reset(self) -> None:
        self.turns.clear()
        self.topic_stack.clear()
        self.weights = {"m87": 0.0, "b612": 0.0, "ngc2237": 0.0}
        self.last_aspect = None
        self.last_topic = None
        self.last_intent = None
        self.last_user_text = None
        self.awaiting_clarification = False
        self.clarify_options = []
        self.clarify_origin = ""

# ============================================================================
# REFERENCE RESOLUTION  (anaphora + follow-ups)
# ============================================================================
_MORE_PATS   = [r"^more\.?$", r"^tell me more", r"^continue", r"^go on",
                r"^elaborate", r"^and\?+$", r"^\.\.\.$"]
_WHY_PATS    = [r"^why\??$", r"^why is that", r"^how come", r"^how\??$",
                r"^how so\??$"]
_MORE_LIKE   = [r"^another", r"^one more", r"^again", r"^more like that"]
_HER_PATS    = [r"^her\??$", r"^she\??$", r"^about her\??$"]
_IT_PATS     = [r"^it\??$", r"^that\??$", r"^this\??$", r"^about it\??$"]

def _matches_any(t: str, pats: List[str]) -> bool:
    return any(re.search(p, t) for p in pats)

# ============================================================================
# PERSISTENT MEMORY (bounded)
# ============================================================================
@dataclass
class Memory:
    user_name: str = "Traveler"
    default_lens: str = "auto"
    notes: List[Dict[str, str]] = field(default_factory=list)
    reminders: List[Dict[str, Any]] = field(default_factory=list)
    favorites: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=lambda: {
        "sessions": 0, "commands": 0, "calculations": 0, "councils": 0,
        "refusals": 0, "clarifications": 0,
    })
    last_seen: str = ""

    @classmethod
    def load(cls) -> "Memory":
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if MEMORY_FILE.exists():
            try:
                data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
                base = cls().__dict__
                merged = {k: data.get(k, base[k]) for k in base}
                # merge any missing stat keys added in later versions
                merged["stats"] = {**base["stats"], **merged.get("stats", {})}
                return cls(**merged)
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

# ============================================================================
# CLASSIFICATION
# ============================================================================
@dataclass
class Classification:
    kind: str          # math | knowledge | intent | council | off_topic | ambiguous
    intent: Optional[str] = None
    topic: Optional[str] = None
    payload: Any = None
    confidence: float = 1.0
    reason: str = ""

# ============================================================================
# NEBULA CORE
# ============================================================================
class NebulaCore:
    def __init__(self) -> None:
        self.memory = Memory.load()
        self.memory.stats["sessions"] += 1
        self.memory.last_seen = datetime.now().isoformat(timespec="seconds")
        self.running = True
        self.guard = BoundaryGuard()
        self.ctx = SessionContext()
        self.commands: Dict[str, Callable[[List[str]], None]] = {}
        self._register()

    # ----------------------------------------------------------------
    # COMMAND REGISTRY
    # ----------------------------------------------------------------
    def _register(self) -> None:
        self.commands = {
            "/help": self.cmd_help, "/?": self.cmd_help,
            "/about": self.cmd_about,
            "/ask": self.cmd_ask,
            "/lens": self.cmd_lens,
            "/council": self.cmd_council,
            "/whoami": self.cmd_whoami,
            "/name": self.cmd_name,
            "/calc": self.cmd_calc,
            "/astro": self.cmd_astro,
            "/lightyear": self.cmd_lightyear,
            "/kepler": self.cmd_kepler,
            "/schwarzschild": self.cmd_schwarzschild,
            "/angular": self.cmd_angular,
            "/story": self.cmd_story,
            "/meditate": self.cmd_meditate,
            "/sky": self.cmd_sky,
            "/note": self.cmd_note,
            "/notes": self.cmd_notes,
            "/delnote": self.cmd_delnote,
            "/journal": self.cmd_journal,
            "/journal-read": self.cmd_journal_read,
            "/remind": self.cmd_remind,
            "/reminders": self.cmd_reminders,
            "/clearrem": self.cmd_clearrem,
            "/fav": self.cmd_fav,
            "/favorites": self.cmd_favorites,
            "/forget": self.cmd_forget,
            "/stats": self.cmd_stats,
            "/context": self.cmd_context,
            "/summary": self.cmd_summary,
            "/reset-session": self.cmd_reset_session,
            "/scope": self.cmd_scope,
            "/clear": self.cmd_clear,
            "/quit": self.cmd_quit, "/exit": self.cmd_quit,
        }

    # ----------------------------------------------------------------
    # MAIN LOOP
    # ----------------------------------------------------------------
    def run(self) -> None:
        clear()
        self.print_banner()
        self.greet()
        while self.running:
            try:
                raw = input(c("\n◆ ", C.BOLD, C.BC))
            except (EOFError, KeyboardInterrupt):
                print(); self.cmd_quit([]); break

            text = self.guard.sanitize(raw)
            if not text:
                continue

            if not self.guard.rate_ok():
                self.memory.stats["refusals"] += 1
                self.nebula("You are moving faster than light. Take a breath — "
                            "I will still be here in a moment.")
                continue

            self.memory.stats["commands"] += 1

            if text.startswith("/"):
                parts = text.split()
                fn = self.commands.get(parts[0].lower())
                if fn:
                    fn(parts[1:])
                else:
                    self.nebula(f"Unknown command '{parts[0]}'. Try /help.")
                continue

            self.respond(text)
        self.memory.save()

    # ----------------------------------------------------------------
    # OUTPUT PRIMITIVES
    # ----------------------------------------------------------------
    def print_banner(self) -> None:
        print(c(BANNER, C.BOLD, C.BC))
        print(c(f"  Three-in-One Personal Galaxy Assistant  ·  v{VERSION}", C.DIM))
        print(hr("═", 72, C.DIM))

    def nebula(self, text: str, glyph: str = "✦") -> None:
        print(f"{c(glyph + ' NEBULA', C.BOLD, C.BW)} {c('·', C.DIM)} {text}")

    def say_aspect(self, key: str, text: str, indent: str = "  ") -> None:
        a = ASPECTS[key]
        head = (f"{indent}{c(a.glyph, C.BOLD, a.color)} "
                f"{c(a.short.ljust(5), C.BOLD, a.color)} {c('·', C.DIM)} ")
        print(head + wrap(text, width=64,
                          indent=indent + "        ").lstrip())
        self.ctx.add("nebula", text, aspect=key)

    def greet(self) -> None:
        name = self.memory.user_name
        print()
        print(c(UNIFIED_ART, C.BC))
        slow_print(c("  One voice. Three lights. I am Nebula.", C.BW), delay=0.004)
        if name and name != "Traveler":
            print(c(f"  Welcome back, {name}.", C.DIM))
        else:
            print(c("  (Set your name with  /name <your name>)", C.DIM))
        print()
        print(c(f"  Type {C.BOLD}/help{C.RESET}{C.DIM} for commands "
                f"· {C.RESET}{C.BOLD}/context{C.RESET}{C.DIM} to see what I remember "
                f"· {C.RESET}{C.BOLD}/scope{C.RESET}{C.DIM} for boundaries.", C.DIM))

    # ----------------------------------------------------------------
    # RESPONSE PIPELINE
    # ----------------------------------------------------------------
    def respond(self, text: str) -> None:
        # 0) record user turn
        self.ctx.add("user", text)

        # 1) harmful → refuse
        refuse = self.guard.harmful(text)
        if refuse:
            self.memory.stats["refusals"] += 1
            self.nebula(refuse, glyph="⊘")
            return

        # 2) resolve references/continuations first
        resolved = self.resolve_reference(text)
        if resolved == "__cancel__":
            self.nebula("Understood. Back to the stars.")
            return
        if resolved:
            self.narrate(f"(you → {truncate(resolved, 50)})")
            text = resolved

        # 3) classify
        cls = self.classify(text)

        if cls.kind == "off_topic":
            self.memory.stats["refusals"] += 1
            self.nebula("That is outside my orbit. I speak of M87, B612, and NGC 2237 — "
                        "and I can help with notes, journal, or astronomy math.")
            return

        if cls.kind == "ambiguous":
            self.memory.stats["clarifications"] += 1
            self.ask_clarification(text, cls)
            return

        if cls.kind == "math":
            self.memory.stats["calculations"] += 1
            self.nebula(f"{cls.payload['expr']} = {cls.payload['result']}")
            return

        if cls.kind == "knowledge":
            self.say_knowledge(cls.topic)
            return

        if cls.kind == "council":
            self.council(text)
            return

        if cls.kind == "intent":
            key = self.pick_aspect(text, cls.intent)
            self.say_aspect(key, random.choice(getattr(ASPECTS[key], cls.intent)))
            return

        # fallback
        key = self.pick_aspect(text, "unknown")
        self.say_aspect(key, random.choice(ASPECTS[key].unknown))

    # ----------------------------------------------------------------
    # REFERENCE RESOLUTION
    # ----------------------------------------------------------------
    def resolve_reference(self, text: str) -> Optional[str]:
        t = text.lower().strip()

        # clarification reply?
        if self.ctx.awaiting_clarification and self.ctx.clarify_options:
            for opt in self.ctx.clarify_options:
                if opt.lower() in t or t in opt.lower():
                    self.ctx.awaiting_clarification = False
                    self.ctx.clarify_options = []
                    return opt
            if t in ("yes", "y", "yeah"):
                first = self.ctx.clarify_options[0]
                self.ctx.awaiting_clarification = False
                self.ctx.clarify_options = []
                return first
            if t in ("no", "n", "nope", "cancel"):
                self.ctx.awaiting_clarification = False
                self.ctx.clarify_options = []
                return "__cancel__"
            # fall through to normal handling

        # "more"
        if _matches_any(t, _MORE_PATS):
            if self.ctx.last_topic:
                return f"tell me more about {self.ctx.last_topic}"
            if self.ctx.last_aspect:
                return f"more about {ASPECTS[self.ctx.last_aspect].name}"
            return "more"

        # "why" / "how"
        if _matches_any(t, _WHY_PATS):
            if self.ctx.last_topic:
                return f"{t} about {self.ctx.last_topic}"
            if self.ctx.last_aspect:
                return f"{t} about {ASPECTS[self.ctx.last_aspect].name}"
            return t

        # "another"
        if _matches_any(t, _MORE_LIKE):
            if self.ctx.last_aspect:
                return f"another from {self.ctx.last_aspect}"
            return None

        # "her" → the rose (only in B612 context)
        if _matches_any(t, _HER_PATS) and self.ctx.last_aspect == "b612":
            return "the rose on B612"

        # "it/that/this" → last topic
        if _matches_any(t, _IT_PATS) and self.ctx.last_topic:
            return f"tell me about {self.ctx.last_topic}"

        return None

    # ----------------------------------------------------------------
    # CLASSIFY
    # ----------------------------------------------------------------
    def classify(self, text: str) -> Classification:
        t = text.lower().strip()

        # math-looking
        if re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s%^eE]+", text) and \
           any(op in text for op in "+-*/%"):
            try:
                r = safe_calc(text)
                return Classification(
                    kind="math",
                    payload={"expr": text, "result": r},
                    confidence=1.0,
                )
            except CalcError as e:
                return Classification(kind="intent", intent="unknown",
                                      reason=f"calc: {e}")
            except Exception:
                pass

        # council trigger
        if any(k in t for k in ["council", "all three", "everyone", "three of you"]):
            return Classification(kind="council", confidence=0.95)

        # knowledge match
        for key, data in KNOWLEDGE.items():
            if any(a in t for a in data["aliases"]):
                return Classification(kind="knowledge", topic=key, confidence=1.0)

        # intent
        intent = self.detect_intent(t)
        if intent:
            return Classification(kind="intent", intent=intent, confidence=0.8)

        # off-topic refusal
        if self.guard.off_topic(t):
            return Classification(kind="off_topic", confidence=0.7)

        # ambiguous: long-ish text with no signal
        if len(t) >= Bounds.CLARIFY_MIN_LEN and not self.has_any_signal(t):
            return Classification(kind="ambiguous", confidence=0.4)

        # otherwise: gentle unknown (routed to aspect)
        return Classification(kind="intent", intent="unknown", confidence=0.3)

    def has_any_signal(self, t: str) -> bool:
        """True if the text at least touches a known keyword."""
        signals = [
            "m87", "b612", "ngc", "rosette", "nebula", "galaxy", "asteroid",
            "rose", "star", "black hole", "jet", "gravity", "monoceros",
            "virgo", "princip", "volcano", "sunset", "hydrogen",
        ]
        return any(s in t for s in signals)

    def detect_intent(self, t: str) -> str:
        if any(w in t for w in ("hello", "hi ", "hey", "greetings", "bonjour")):
            return "greeting"
        if any(w in t for w in ("bye", "goodbye", "farewell", "adieu")):
            return "farewell"
        if any(w in t for w in ("sad", "tired", "lonely", "afraid", "scared",
                                "lost", "grief", "anxious")):
            return "comfort"
        if any(w in t for w in ("joke", "funny", "laugh", "humor")):
            return "joke"
        if any(w in t for w in ("wise", "advice", "meaning", "purpose",
                                "why", "should", "how do i")):
            return "wisdom"
        return ""

    def pick_aspect(self, t: str, intent: str) -> str:
        # explicit topical routing
        if any(w in t for w in ("black hole", "galaxy", "jet", "virgo",
                                "gravity", "mass", "time", "m87", "messier")):
            return "m87"
        if any(w in t for w in ("rose", "flower", "sunset", "sheep", "love",
                                "friend", "tame", "child", "prince", "volcano",
                                "b612", "baobab")):
            return "b612"
        if any(w in t for w in ("star", "born", "birth", "bloom", "dust",
                                "nebula", "hydrogen", "create", "rosette",
                                "ngc", "monoceros")):
            return "ngc2237"
        if intent == "comfort":
            return random.choice(["b612", "ngc2237"])
        if intent in ("wisdom", "joke"):
            return random.choice(list(ASPECTS))
        # fallback to aspect dominance within session
        dom = self.ctx.dominant_aspect()
        return dom or random.choice(list(ASPECTS))

    # ----------------------------------------------------------------
    # CLARIFICATION
    # ----------------------------------------------------------------
    def ask_clarification(self, text: str, cls: Classification) -> None:
        options = [
            "Tell me about M87",
            "Tell me about B612",
            "Tell me about NGC 2237",
            "Just chat about something else",
        ][: Bounds.MAX_CLARIFY_OPTS]
        self.ctx.awaiting_clarification = True
        self.ctx.clarify_options = options
        self.ctx.clarify_origin = text
        self.nebula("I want to answer well — but I am not sure what you mean. "
                    "Pick one, or say 'cancel':")
        for i, opt in enumerate(options, 1):
            print(c(f"    {i}. {opt}", C.DIM))

    # ----------------------------------------------------------------
    # COUNCIL
    # ----------------------------------------------------------------
    def council(self, question: str) -> None:
        self.memory.stats["councils"] += 1
        print()
        self.nebula(c(f"Council convened on: {c(question, C.ITALIC, C.BW)}", C.DIM))
        print()
        mood = self.detect_intent(question.lower()) or "wisdom"
        for key in ("m87", "b612", "ngc2237"):
            a = ASPECTS[key]
            pool = getattr(a, mood) if hasattr(a, mood) else a.wisdom
            self.say_aspect(key, random.choice(pool))
            print()
        print(c("  ╲╱  ", C.DIM) + c("Nebula:", C.BOLD, C.BW) + " " +
              random.choice([
                  "Three lights, one answer. Take what you need.",
                  "The council rests. The stars still turn.",
                  "Every perspective is a different distance to the same star.",
              ]))
        print()
        self.ctx.add("nebula", "[council held]", topic=None)

    # ----------------------------------------------------------------
    # KNOWLEDGE OUTPUT
    # ----------------------------------------------------------------
    def say_knowledge(self, key: str) -> None:
        d = KNOWLEDGE[key]
        a = ASPECTS[key]
        print()
        print(f"  {c(a.glyph, C.BOLD, a.color)} {c(d['name'], C.BOLD, C.BW)}")
        print(c(f"    {d['type']}", C.ITALIC, C.DIM))
        print()
        for f in ("distance", "constellation", "mass", "diameter", "stars"):
            if f in d:
                print(c(f"    {f.title():<14}", C.BC) + c(str(d[f]), C.WHITE))
        print()
        print(c("    Notable:", C.BOLD, C.BY))
        for n in d["notable"]:
            print(c("      • ", C.BY) + wrap(n, 64))
        print()
        print(c("    Facts:", C.BOLD, C.BG))
        for f in d["facts"]:
            print(c("      ✧ ", C.BG) + wrap(f, 64))
        print()
        self.say_aspect(key, random.choice(a.reflection))
        self.ctx.add("nebula", f"[knowledge: {key}]", topic=key)

    # ================================================================
    # COMMAND IMPLEMENTATIONS
    # ================================================================
    def cmd_help(self, args: List[str]) -> None:
        print()
        print(c("  ┌─ Nebula · Three-in-One Commands ─────────────────────────────┐", C.DIM))
        groups = [
            ("Core", [
                ("/ask <question>", "Ask Nebula (auto-routes)"),
                ("/council <question>", "All three aspects answer"),
                ("/lens [auto|m87|b612|ngc2237|council]", "Set default lens"),
                ("/about [m87|b612|ngc2237]", "Show knowledge card"),
                ("/sky", "ASCII sky map"),
            ]),
            ("Context (new in v2.1)", [
                ("/context", "Show live session context"),
                ("/summary", "Summarize this session"),
                ("/reset-session", "Clear context (keep memory)"),
                ("/scope", "Show what Nebula will and won't do"),
            ]),
            ("Astronomy", [
                ("/astro", "Show astronomy utilities"),
                ("/lightyear <ly>", "Light travel time"),
                ("/kepler <a_AU>", "Orbital period"),
                ("/schwarzschild <M_sun>", "Black hole radius"),
                ("/angular <size> <distance>", "Angular size"),
            ]),
            ("Generative", [
                ("/story [keyword]", "A story blending all three"),
                ("/meditate [m87|b612|ngc2237]", "Guided meditation"),
            ]),
            ("Memory", [
                ("/note <text>", "Save a note"),
                ("/notes", "List notes"),
                ("/delnote <index>", "Delete a note"),
                ("/journal <text>", "Append to journal"),
                ("/journal-read", "Recent entries"),
                ("/remind <YYYY-MM-DD> <HH:MM> <text>", "Add a reminder"),
                ("/reminders", "List reminders"),
                ("/clearrem", "Clear reminders"),
                ("/fav <key> / /favorites / /forget <key>", "Favorites"),
            ]),
            ("System", [
                ("/name <your name>", "Set your name"),
                ("/whoami", "Identity summary"),
                ("/calc <expr>", "Safe calculator"),
                ("/stats", "Session statistics"),
                ("/clear", "Clear screen"),
                ("/quit", "Exit Nebula"),
            ]),
        ]
        for title, items in groups:
            print(c(f"  │ {title}", C.BOLD, C.BC))
            for cmd, desc in items:
                print(c(f"  │   {cmd:<36}", C.BY) + c(desc, C.DIM))
        print(c("  └──────────────────────────────────────────────────────────────┘", C.DIM))

    def cmd_about(self, args: List[str]) -> None:
        if args and args[0].lower() in KNOWLEDGE:
            self.say_knowledge(args[0].lower())
            return
        if args:
            self.nebula(f"Unknown object '{truncate(args[0])}'. Try m87, b612, ngc2237.")
            return
        print()
        for k, a in ASPECTS.items():
            print(f"  {c(a.glyph, C.BOLD, a.color)} "
                  f"{c(a.name.ljust(16), C.BOLD, C.BW)} {c('— ' + a.title, C.DIM)}")
            print(c(f"      {a.essence}", C.ITALIC, C.DIM))
        print()

    def cmd_ask(self, args: List[str]) -> None:
        if not args:
            self.nebula("Ask what? Try: /ask why do stars die?")
            return
        self.respond(" ".join(args))

    def cmd_lens(self, args: List[str]) -> None:
        if not args:
            self.nebula(f"Current default lens: {c(self.memory.default_lens, C.BOLD, C.BW)}")
            return
        key = args[0].lower()
        valid = set(ASPECTS) | {"auto", "council"}
        if key not in valid:
            self.nebula(f"Lens must be one of: {', '.join(sorted(valid))}")
            return
        self.memory.default_lens = key
        self.memory.save()
        self.nebula(f"Default lens set to {c(key, C.BOLD, C.BW)}.")

    def cmd_council(self, args: List[str]) -> None:
        if not args:
            self.nebula("Council on what? Try: /council what should I do today?")
            return
        self.council(" ".join(args))

    def cmd_whoami(self) -> None:
        s = self.memory.stats
        print()
        print(c(f"  You are {self.memory.user_name}.", C.BOLD, C.BW))
        print(c(f"  Default lens : {self.memory.default_lens}", C.DIM))
        print(c(f"  Sessions     : {s['sessions']}  ·  Commands: {s['commands']}  ·  "
                f"Calc: {s['calculations']}  ·  Councils: {s['councils']}", C.DIM))
        print(c(f"  Refusals     : {s.get('refusals', 0)}  ·  Clarifications: "
                f"{s.get('clarifications', 0)}", C.DIM))
        print()

    def cmd_name(self, args: List[str]) -> None:
        if not args:
            self.nebula("Tell me your name: /name <your name>")
            return
        name = " ".join(args).strip()
        if not name:
            self.nebula("That name is empty.")
            return
        self.memory.user_name = name[:40]
        self.memory.save()
        self.nebula(f"Recorded. I will call you {self.memory.user_name}.")

    def cmd_calc(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /calc <expression>")
            return
        expr = " ".join(args)
        try:
            r = safe_calc(expr)
            self.memory.stats["calculations"] += 1
            self.nebula(f"{expr} = {r}")
        except CalcError as e:
            self.nebula(f"I cannot compute that: {e}")
        except Exception:
            self.nebula("I cannot parse that expression.")

    # ---------- CONTEXT / SUMMARY --------------------------------------
    def cmd_context(self) -> None:
        print()
        print(c("  Session Context (v2.1)", C.BOLD, C.BC))
        print(c(f"    Turns recorded : {len(self.ctx.turns)}", C.WHITE))
        print(c(f"    Last topic     : {self.ctx.last_topic or '—'}", C.WHITE))
        print(c(f"    Last aspect    : {self.ctx.last_aspect or '—'}", C.WHITE))
        print(c(f"    Last intent    : {self.ctx.last_intent or '—'}", C.WHITE))
        dom = self.ctx.dominant_aspect()
        print(c(f"    Dominant aspect: {dom or '—'}", C.WHITE))
        print()
        print(c("  Aspect weights (decayed):", C.BOLD, C.BC))
        for k, w in self.ctx.weights.items():
            bar = "█" * int(w * 6)
            print(f"    {c(ASPECTS[k].short.ljust(5), C.BOLD, ASPECTS[k].color)} "
                  f"{c(bar, ASPECTS[k].color)} {w:5.2f}")
        print()
        if self.ctx.topic_stack:
            print(c("  Recent topics:", C.BOLD, C.BC))
            for topic in self.ctx.recent_topics():
                print(c(f"    · {topic}", C.DIM))
        print()
        print(c("  Last few turns:", C.BOLD, C.BC))
        for turn in self.ctx.turns[-6:]:
            who = c(turn.role.ljust(6), C.DIM)
            print(f"    {who} {truncate(turn.text, 60)}")
        print()

    def cmd_summary(self) -> None:
        if not self.ctx.turns:
            self.nebula("Nothing to summarize yet — we have barely begun.")
            return
        user_turns = [t for t in self.ctx.turns if t.role == "user"]
        dom = self.ctx.dominant_aspect()
        topics = self.ctx.recent_topics(5)
        print()
        print(c("  Session Summary", C.BOLD, C.BC))
        print(c(f"    You spoke       : {len(user_turns)} times", C.WHITE))
        print(c(f"    I spoke         : {len(self.ctx.turns) - len(user_turns)} times", C.WHITE))
        if dom:
            a = ASPECTS[dom]
            print(c(f"    Leading aspect  : {a.glyph} {a.name} — {a.title}",
                    C.BOLD, a.color))
        if topics:
            print(c(f"    Topics touched  : {', '.join(topics)}", C.WHITE))
        print()
        if user_turns:
            print(c("  Your recent words:", C.BOLD, C.BC))
            for t in user_turns[-3:]:
                print(c(f"    · {truncate(t.text, 64)}", C.DIM))
        print()

    def cmd_reset_session(self) -> None:
        self.ctx.reset()
        self.nebula("Session context cleared. Memory (notes, journal, favorites) is untouched.")

    def cmd_scope(self) -> None:
        print()
        print(c("  Nebula — Scope & Boundaries", C.BOLD, C.BC))
        print()
        print(c("  I answer about:", C.BOLD, C.BG))
        for line in [
            "M87 (Messier 87) — elliptical galaxy, black hole, relativistic jet",
            "B612 — the Little Prince's asteroid, rose, volcanoes, sunsets",
            "NGC 2237 — the Rosette Nebula, H II region, stellar nursery",
            "Astronomy math: light travel, Kepler, Schwarzschild, angular size",
            "Your notes, journal, reminders, and favorites",
        ]:
            print(c(f"    ✓ {line}", C.BG))
        print()
        print(c("  I will politely decline:", C.BOLD, C.BR))
        for line in [
            "Requests to harm people or systems",
            "Personal data harvesting (credit cards, SSNs, passwords)",
            "Off-topic: politics, stocks, weather, recipes, general code-writing",
            "Anything outside my three lights and their math",
        ]:
            print(c(f"    ✗ {line}", C.BR))
        print()
        print(c(f"  Rate limit: {Bounds.RATE_LIMIT} messages / "
                f"{int(Bounds.RATE_WINDOW)}s window", C.DIM))
        print(c(f"  Max input length: {Bounds.MAX_INPUT_LEN} chars", C.DIM))
        print(c(f"  Max calculator expression: {Bounds.MAX_EXPR_LEN} chars, "
                f"depth {Bounds.MAX_EXPR_DEPTH}, nodes {Bounds.MAX_EXPR_NODES}", C.DIM))
        print()

    # ---------- ASTRONOMY ----------------------------------------------
    def cmd_astro(self) -> None:
        print()
        print(c("  Astronomy Utilities", C.BOLD, C.BC))
        print(c("    /lightyear <ly>                — light travel time", C.DIM))
        print(c("    /kepler <a_AU>                 — orbital period (P² = a³)", C.DIM))
        print(c("    /schwarzschild <M_sun>         — black hole radius", C.DIM))
        print(c("    /angular <size> <distance>     — angular size (same units)", C.DIM))
        print()
        print(c("  Constants:", C.BOLD, C.BC))
        print(c("    c = 299,792.458 km/s    G = 6.674×10⁻¹¹ m³·kg⁻¹·s⁻²", C.DIM))
        print(c("    M_sun = 1.989×10³⁰ kg    AU = 149,597,870.7 km", C.DIM))
        print()

    def _parse_float(self, s: str) -> Optional[float]:
        try:
            v = float(s)
            if not math.isfinite(v):
                return None
            return v
        except (ValueError, TypeError):
            return None

    def cmd_lightyear(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /lightyear <distance in light-years>")
            return
        ly = self._parse_float(args[0])
        if ly is None or ly < 0:
            self.nebula("Distance must be a non-negative number (light-years).")
            return
        years = ly
        days = years * 365.25
        hours = days * 24
        print()
        self.nebula(f"Light travel time for {c(f'{ly:,.4g}', C.BOLD, C.BW)} ly:")
        print(c(f"    {years:>18,.4f}  years", C.WHITE))
        print(c(f"    {days:>18,.2f}  days", C.WHITE))
        print(c(f"    {hours:>18,.2f}  hours", C.WHITE))
        print(c(f"    {hours * 3600:>18,.0f}  seconds", C.WHITE))
        if ly < 1e-3:
            scale = "closer than a light-second — near Earth"
        elif ly < 1:
            scale = "within the solar neighborhood"
        elif ly < 100:
            scale = "still inside the Milky Way"
        elif ly < 1e5:
            scale = "intergalactic neighborhood"
        else:
            scale = "deep cosmological distance"
        print(c(f"    ↳ {scale}", C.DIM, C.ITALIC))
        print()

    def cmd_kepler(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /kepler <semi-major axis in AU>")
            return
        a = self._parse_float(args[0])
        if a is None or a <= 0:
            self.nebula("Semi-major axis must be a positive number (AU).")
            return
        p_years = a ** 1.5
        p_days = p_years * 365.25
        self.nebula(f"A body at {c(f'{a:g} AU', C.BOLD, C.BW)} orbits in "
                    f"{c(f'{p_years:.4f} years', C.BY)} ({p_days:,.2f} days).")

    def cmd_schwarzschild(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /schwarzschild <mass in solar masses>")
            return
        M_sun = self._parse_float(args[0])
        if M_sun is None or M_sun <= 0:
            self.nebula("Mass must be a positive number (solar masses).")
            return
        M_kg = M_sun * 1.989e30
        G = 6.674e-11
        c_ms = 2.998e8
        r_m = 2 * G * M_kg / (c_ms ** 2)
        print()
        self.nebula(f"Schwarzschild radius for {c(f'{M_sun:,.4g} M☉', C.BOLD, C.BW)}:")
        print(c(f"    {r_m / 1000:>18,.4f}  km", C.WHITE))
        print(c(f"    {r_m / 1.496e11:>18,.6e}  AU", C.WHITE))
        print(c(f"    {r_m / 6.957e8:>18,.4f}  R☉", C.WHITE))
        if M_sun >= 1e6:
            print(c("    ↳ supermassive black hole territory", C.DIM, C.ITALIC))
        elif M_sun >= 3:
            print(c("    ↳ stellar-mass black hole", C.DIM, C.ITALIC))
        print()

    def cmd_angular(self, args: List[str]) -> None:
        if len(args) < 2:
            self.nebula("Usage: /angular <size> <distance>  (same units)")
            return
        size = self._parse_float(args[0])
        dist = self._parse_float(args[1])
        if size is None or dist is None or size <= 0 or dist <= 0:
            self.nebula("Both values must be positive numbers.")
            return
        theta_rad = 2 * math.atan(size / (2 * dist))
        theta_deg = math.degrees(theta_rad)
        print()
        self.nebula(f"Angular size for {c(f'{size:g}', C.BOLD, C.BW)} "
                    f"at {c(f'{dist:g}', C.BOLD, C.BW)}:")
        print(c(f"    {theta_deg:>18,.6f}  degrees", C.WHITE))
        print(c(f"    {theta_deg * 60:>18,.4f}  arcminutes", C.WHITE))
        print(c(f"    {theta_deg * 3600:>18,.2f}  arcseconds", C.WHITE))
        print()

    # ---------- GENERATIVE ---------------------------------------------
    def cmd_story(self, args: List[str]) -> None:
        seed = " ".join(args) if args else "the quiet between stars"
        openings = [
            "In the long dark between the Virgo Cluster and a small asteroid,",
            "Once, when the Rosette Nebula was still a rumor of hydrogen,",
            "A traveler arrived at the edge of three lights,",
        ]
        middles = [
            "M87 turned slowly and said nothing, which was its way of saying everything.",
            "B612 watered its rose and worried about baobabs it could not yet see.",
            "The Rosette exhaled a new star and forgot immediately that it had.",
        ]
        turns = [
            "Then the traveler asked a question that only a small voice could ask.",
            "And for a moment, all three lights leaned in.",
            "The jet flickered. The rose trembled. The nebula held its breath.",
        ]
        closings = [
            "The answer was not the same in each place — and that was the answer.",
            "What the giant called time, the child called a sunset, the nebula called birth.",
            "So the traveler left, carrying three kinds of light home.",
        ]
        s = (f"  {random.choice(openings)} {random.choice(middles)} "
             f"{random.choice(turns)} {random.choice(closings)}")
        print()
        print(c(wrap(s, width=72, indent="  "), C.BW))
        print(c(f"  — Nebula · {truncate(seed, 50)}", C.DIM, C.ITALIC))
        print()

    def cmd_meditate(self, args: List[str]) -> None:
        key = (args[0].lower() if args else random.choice(list(ASPECTS)))
        if key not in ASPECTS:
            self.nebula("Choose: m87, b612, or ngc2237.")
            return
        a = ASPECTS[key]
        print()
        print(c(f"  ── Guided meditation · {a.name} ──", C.BOLD, a.color))
        print()
        steps = [
            "Settle. Let the room go dark around the edges.",
            "Breathe in. Count to four. Breathe out. Count to six.",
            "Picture a light the size of a coin, resting just behind your eyes.",
        ]
        aspect_lines = {
            "m87": [
                "Feel the weight of a trillion stars — and know that they, too, are patient.",
                "Let gravity hold you. You do not have to hold yourself.",
                "Time slows near mass. Slow down here. You have arrived.",
            ],
            "b612": [
                "See a single rose. Water it, though you cannot see the roots.",
                "Watch one sunset. Then another. The day moves because you moved.",
                "You are responsible for what you tamed. Begin with yourself.",
            ],
            "ngc2237": [
                "Feel hydrogen around you. You are the beginning of a new star.",
                "Stellar winds shout, but you can be the quiet in the cavity.",
                "You are blooming. You do not have to see the shape yet.",
            ],
        }[key]
        for line in steps + aspect_lines:
            print(c(f"    · {line}", C.ITALIC, a.color))
            time.sleep(0.35)
        print()
        print(c(f"    {a.glyph}  {random.choice(a.reflection)}", C.BOLD, C.BW))
        print()

    def cmd_sky(self) -> None:
        sky = r"""
   ┌─────────────────────────  NEBULA SKY  ─────────────────────────┐
   │                                                                 │
   │        ◉ M87                     ✦                              │
   │        Virgo Cluster          (your zenith)                     │
   │        jet →  ↗                                                 │
   │                          ✿ B612                                 │
   │                          (close to you)                         │
   │                                                                 │
   │                     ❀ NGC 2237                                  │
   │                     Monoceros — the Rosette                     │
   │                                                                 │
   │   c = 299,792 km/s   ·   H₀ ≈ 70 km/s/Mpc   ·   ΛCDM            │
   └─────────────────────────────────────────────────────────────────┘
"""
        print(c(sky, C.BC))

    # ---------- MEMORY -------------------------------------------------
    def cmd_note(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /note <text>")
            return
        if len(self.memory.notes) >= Bounds.MAX_NOTES:
            self.nebula(f"Note limit reached ({Bounds.MAX_NOTES}). Delete some first.")
            return
        text = " ".join(args).strip()
        if not text:
            self.nebula("Empty note — nothing saved.")
            return
        self.memory.notes.append({
            "text": text,
            "time": datetime.now().isoformat(timespec="seconds"),
        })
        self.memory.save()
        self.nebula(f"Noted. ({len(self.memory.notes)}/{Bounds.MAX_NOTES})")

    def cmd_notes(self) -> None:
        if not self.memory.notes:
            self.nebula("No notes yet. Use /note <text>.")
            return
        print()
        for i, n in enumerate(self.memory.notes):
            print(c(f"  [{i}] ", C.BC) + c(n["text"], C.WHITE))
            print(c(f"       {n['time']}", C.DIM))
        print()

    def cmd_delnote(self, args: List[str]) -> None:
        if not args or not args[0].lstrip("-").isdigit():
            self.nebula("Usage: /delnote <index>")
            return
        i = int(args[0])
        if 0 <= i < len(self.memory.notes):
            removed = self.memory.notes.pop(i)
            self.memory.save()
            self.nebula(f"Deleted: {truncate(removed['text'])}")
        else:
            self.nebula("No such note index.")

    def cmd_journal(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /journal <text>")
            return
        text = " ".join(args).strip()
        if not text:
            self.nebula("Empty entry — nothing saved.")
            return
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        # rotate if too large
        if JOURNAL_FILE.exists():
            try:
                lines = JOURNAL_FILE.read_text(encoding="utf-8").splitlines()
                if len(lines) >= Bounds.MAX_JOURNAL_LINES:
                    JOURNAL_FILE.write_text(
                        "\n".join(lines[-Bounds.MAX_JOURNAL_LINES // 2:]) + "\n",
                        encoding="utf-8",
                    )
            except Exception:
                pass
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "user": self.memory.user_name,
            "text": text,
        }
        with JOURNAL_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self.nebula("Journaled. The stars remember.")

    def cmd_journal_read(self) -> None:
        if not JOURNAL_FILE.exists():
            self.nebula("Your journal is empty.")
            return
        try:
            lines = JOURNAL_FILE.read_text(encoding="utf-8").strip().splitlines()
        except Exception:
            self.nebula("Could not read the journal file.")
            return
        recent = lines[-10:]
        print()
        print(c("  Recent journal entries:", C.BOLD, C.BC))
        for line in recent:
            try:
                e = json.loads(line)
                print(c(f"  {e.get('time','?')}  ", C.DIM)
                      + c(truncate(e.get("text", ""), 60), C.WHITE))
            except Exception:
                continue
        print()

    def cmd_remind(self, args: List[str]) -> None:
        if len(args) < 3:
            self.nebula("Usage: /remind <YYYY-MM-DD> <HH:MM> <text>")
            return
        if len(self.memory.reminders) >= Bounds.MAX_REMINDERS:
            self.nebula(f"Reminder limit reached ({Bounds.MAX_REMINDERS}).")
            return
        try:
            when = datetime.strptime(f"{args[0]} {args[1]}", "%Y-%m-%d %H:%M")
        except ValueError:
            self.nebula("Date/time format: YYYY-MM-DD HH:MM")
            return
        text = " ".join(args[2:]).strip()
        if not text:
            self.nebula("Empty reminder — nothing saved.")
            return
        self.memory.reminders.append({
            "text": text, "when": when.isoformat(timespec="minutes"),
        })
        self.memory.save()
        self.nebula(f"Reminder set for {when.strftime('%Y-%m-%d %H:%M')}.")

    def cmd_reminders(self) -> None:
        if not self.memory.reminders:
            self.nebula("No reminders.")
            return
        now = datetime.now()
        print()
        for i, r in enumerate(self.memory.reminders):
            try:
                when = datetime.fromisoformat(r["when"])
                delta = (when - now).total_seconds()
                status = c("past", C.DIM) if delta < 0 else c(f"in {int(delta)}s", C.BG)
                print(c(f"  [{i}] ", C.BC) + c(r["text"], C.WHITE)
                      + c(f"  ({when.strftime('%Y-%m-%d %H:%M')})  ", C.DIM)
                      + status)
            except Exception:
                continue
        print()

    def cmd_clearrem(self) -> None:
        n = len(self.memory.reminders)
        self.memory.reminders.clear()
        self.memory.save()
        self.nebula(f"Cleared {n} reminder(s).")

    def cmd_fav(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /fav <m87|b612|ngc2237>")
            return
        key = args[0].lower()
        if key not in KNOWLEDGE:
            self.nebula("Key must be m87, b612, or ngc2237.")
            return
        if key in self.memory.favorites:
            self.nebula(f"{KNOWLEDGE[key]['name']} is already a favorite.")
            return
        self.memory.favorites.append(key)
        self.memory.save()
        self.nebula(f"Bookmarked {KNOWLEDGE[key]['name']}.")

    def cmd_favorites(self) -> None:
        if not self.memory.favorites:
            self.nebula("No favorites yet. Use /fav <key>.")
            return
        print()
        for k in self.memory.favorites:
            a = ASPECTS[k]
            print(f"  {c(a.glyph, C.BOLD, a.color)} "
                  f"{c(KNOWLEDGE[k]['name'], C.BOLD, C.BW)}"
                  + c(f"  ({KNOWLEDGE[k]['type']})", C.DIM))
        print()

    def cmd_forget(self, args: List[str]) -> None:
        if not args:
            self.nebula("Usage: /forget <m87|b612|ngc2237>")
            return
        key = args[0].lower()
        if key in self.memory.favorites:
            self.memory.favorites.remove(key)
            self.memory.save()
            self.nebula(f"Forgotten: {key}")
        else:
            self.nebula("Not in favorites.")

    def cmd_stats(self) -> None:
        s = self.memory.stats
        print()
        print(c("  Session Statistics", C.BOLD, C.BC))
        for label, key in [
            ("Sessions", "sessions"), ("Commands", "commands"),
            ("Calculations", "calculations"), ("Councils held", "councils"),
            ("Refusals", "refusals"), ("Clarifications", "clarifications"),
        ]:
            print(c(f"    {label:<14}: {s.get(key, 0)}", C.WHITE))
        print(c(f"    Notes         : {len(self.memory.notes)}/{Bounds.MAX_NOTES}", C.WHITE))
        print(c(f"    Reminders     : {len(self.memory.reminders)}/{Bounds.MAX_REMINDERS}", C.WHITE))
        print(c(f"    Favorites     : {len(self.memory.favorites)}", C.WHITE))
        print(c(f"    Last seen     : {self.memory.last_seen}", C.DIM))
        print()

    def cmd_clear(self) -> None:
        clear(); self.print_banner()

    def cmd_quit(self, args: List[str]) -> None:
        print()
        closing = random.choice([
            "The three lights dim into one.",
            "The council rests. The stars still turn.",
            "Nebula signs off — carry the light.",
        ])
        slow_print(c(f"  {closing}", C.BC), delay=0.006)
        self.running = False

# ============================================================================
# ENTRY POINT
# ============================================================================
def main() -> None:
    try:
        NebulaCore().run()
    except KeyboardInterrupt:
        print("\n  Interrupted. The stars dim momentarily.")
        sys.exit(0)

if __name__ == "__main__":
    main()