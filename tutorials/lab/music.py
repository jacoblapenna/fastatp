"""The orchestra's record types: the conductor's Play, and the Tone a member writes back.

Tone starts from guide one's Note and adds what a sound needs. Every number in
here is a whole number with its scale written beside it, because the atproto
data model has no floats.
"""

from fractions import Fraction
from typing import Annotated

from lexicons import Note
from pydantic import BaseModel, Field

from fastatp import Lexicon
from fastatp.lex.types import AtUri, Datetime, Did, MaxGraphemes

# A frequency is two integers: hertz = pitch x 10^pitch_exponent. Middle C,
# 261.626 Hz, is pitch 261626 with exponent -3, counted in millihertz. The
# exponent is data in the record, like the pitch, so the writer says how
# precise the number is and every reader, in any language, gets exactly that
# number back. A float would leave the precision to whoever printed it last.
Pitch = Annotated[int, Field(ge=1, description="the frequency, in units of 10^pitch_exponent Hz")]
PitchExponent = Annotated[int, Field(description="the power of ten pitch counts in: -3 is mHz")]
# A duration needs no exponent of its own: the unit is in the name.
DurationMs = Annotated[int, Field(ge=1, le=60_000, description="how long the note sounds, in ms")]


class Part(BaseModel):
    """One member's share of a Play: who plays, and what."""

    member: Did  # the member's DID; a Play may name one member or several
    text: Annotated[str, MaxGraphemes(300)]  # what the member's Tone will say, such as "do"
    pitch: Pitch
    pitch_exponent: PitchExponent
    duration_ms: DurationMs


class Play(Lexicon):
    """The conductor, in its own repo: these members, these notes."""

    nsid = "com.example.tutorial.play"
    parts: Annotated[list[Part], Field(min_length=1)]  # one part is a note, three are a chord
    created_at: Datetime


class Tone(Note):
    """A member, in its own repo: the note it played for a Play.

    text and created_at come from Note, unchanged. Subclassing a Lexicon keeps
    its fields and rules; the new nsid makes it a record type of its own.
    """

    nsid = "com.example.tutorial.tone"
    play: AtUri  # the Play this Tone answers, in the conductor's repo
    pitch: Pitch
    pitch_exponent: PitchExponent
    duration_ms: DurationMs


def hertz(pitch: int, exponent: int) -> Fraction:
    """The frequency exactly: a Fraction, never a float, so every peer agrees on it."""
    return pitch * Fraction(10) ** exponent
