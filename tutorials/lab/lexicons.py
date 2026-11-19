"""Our own record types. FastATP ships none of these: the namespace is ours."""

from typing import Annotated

from fastatp import Lexicon
from fastatp.lex.types import Datetime, MaxGraphemes

# A Lexicon is a record schema. Subclassing it does two jobs at once. The class
# becomes a validator: building a Note that breaks a rule raises, before anything
# is signed. And importing this module registers the nsid, so any script that
# imports lexicons can turn raw bytes off the wire back into a Note or a Reply.


class Note(Lexicon):
    # The nsid is the record type's name, reversed-domain style so two projects
    # never collide. Every Note in the repo is filed under this collection.
    nsid = "com.example.tutorial.note"
    # 300 grapheme clusters, not 300 Python characters: an emoji built from
    # several code points counts as one. This is a lexicon rule, checked on write.
    text: Annotated[str, MaxGraphemes(300)]
    created_at: Datetime  # must parse as an ISO-8601 timestamp; the type checks that


class Reply(Lexicon):
    nsid = "com.example.tutorial.reply"
    to: str  # the at:// URI of the note being answered; a plain string, nothing is looked up
    text: Annotated[str, MaxGraphemes(300)]
    created_at: Datetime
