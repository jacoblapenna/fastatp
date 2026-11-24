"""The orchestra's record types: the conductor's Tempo and Play, and the Tone a member writes back.

The conductor says what to play: which member, which note, how long. Each
member decides how: its own table turns a note into a pitch. Tone starts
from guide one's Note and adds what that member actually played. Every number
in here is a whole number with its scale written down, because the atproto
data model has no floats.
"""

from fractions import Fraction
from typing import Annotated

from lexicons import Note
from pydantic import BaseModel, Field

from fastatp import Lexicon
from fastatp.lex.types import AtUri, Did, MaxGraphemes


class Tempo(Lexicon):
    """The conductor, in its own repo, for nobody in particular: how long one tick lasts.

    Every Play counts its notes in ticks, and every member reads the Tempo
    in force to turn ticks into time. Changing it changes the speed of
    everything played after it.
    """

    nsid = "com.example.tutorial.tempo"
    tick_ms: Annotated[int, Field(ge=1, le=60_000, description="how long one tick lasts, in ms")]


class Part(BaseModel):
    """One member's share of a Play: who plays which note, and for how many ticks."""

    member: Did  # the member's DID; a Play may name one member or several
    note: Annotated[str, MaxGraphemes(8)]  # a name like "do" or "sol"; members decide its pitch
    # Each octave doubles the pitch: the "do" in octave 5 is twice the "do" in
    # octave 4, and the same note to the ear, only higher.
    # No upper bound: which octaves are worth playing is a member's rule, not the record's.
    octave: Annotated[int, Field(ge=0)]
    ticks: Annotated[int, Field(ge=1, le=64)]  # how long, counted in the Tempo's ticks


class Play(Lexicon):
    """The conductor, in its own repo: these members, these notes."""

    nsid = "com.example.tutorial.play"
    # No created_at: the record key is a TID, a timestamp already, and the
    # record in the guide is then exactly the record that gets signed.
    parts: Annotated[list[Part], Field(min_length=1)]  # one part is a note, three are a chord


# A frequency is two integers: hertz = pitch x 10^pitch_exponent. Middle C,
# 261.626 Hz, is pitch 261626 with exponent -3, counted in millihertz. The
# exponent is data in the record, like the pitch, so the writer says how
# precise the number is and every reader, in any language, gets exactly that
# number back. A float would leave the precision to whoever printed it last.
Pitch = Annotated[int, Field(ge=1, description="the frequency, in units of 10^pitch_exponent Hz")]
PitchExponent = Annotated[int, Field(description="the power of ten pitch counts in: -3 is mHz")]


class Tone(Note):
    """A member, in its own repo: the note it played for a Play, as it played it.

    text and created_at come from Note, unchanged. Subclassing a Lexicon keeps
    its fields and rules; the new nsid makes it a record type of its own.
    """

    nsid = "com.example.tutorial.tone"
    play: AtUri  # the Play this Tone answers, in the conductor's repo
    pitch: Pitch
    pitch_exponent: PitchExponent
    duration_ms: Annotated[int, Field(ge=1, description="how long it sounded, in ms")]


def hertz(pitch: int, exponent: int) -> Fraction:
    """The frequency exactly: a Fraction, never a float, so every peer agrees on it."""
    return pitch * Fraction(10) ** exponent
