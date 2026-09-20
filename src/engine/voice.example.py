"""Template for src/engine/voice.py, which is not in version control.

Copy this to voice.py and write the prompts. Without it, narration is skipped
and each fight card falls back to its placeholder line, so nothing else breaks.
The key named by KEY_ENV must be in .env.
"""

VERSION = "v1"
PROVIDER = "gemini"          # "gemini" or "anthropic"
MODELS = ("gemini-3.5-flash",)
KEY_ENV = "API_KEY"
TEMPERATURE = 1.0
WORDS = (45, 75)

SYSTEM = """<how the paragraph should sound, what it must cover, what it must
never do, and the length in words: {low} to {high}>"""

USER = """<the instruction that carries the fact sheet>

{sheet}"""


def system_prompt():
    return SYSTEM.format(low=WORDS[0], high=WORDS[1])
