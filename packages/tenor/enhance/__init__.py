"""Stage 1 of the Concentrate engine: speaker attribution and entity linking, without an LLM.

``enhance(note, directory, history)`` returns an :class:`EnhancedNote`: a resolved speaker (or an
explicit, candidate-listing "unresolved") for every utterance, person/org/project mention links,
and the attendees resolved to directory ids. Every decision carries a confidence and a method.
"""

from tenor.enhance.directory import Directory, DirectoryOrg, DirectoryPerson, DirectoryProject
from tenor.enhance.enhancer import enhance, enhance_all, enhanced_line, speaker_label
from tenor.enhance.models import EnhancedNote, MentionLink, SpeakerResolution

__all__ = [
    "Directory",
    "DirectoryOrg",
    "DirectoryPerson",
    "DirectoryProject",
    "EnhancedNote",
    "MentionLink",
    "SpeakerResolution",
    "enhance",
    "enhance_all",
    "enhanced_line",
    "speaker_label",
]
