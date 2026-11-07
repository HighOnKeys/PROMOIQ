"""Transparent cue lexicons for the offline (heuristic) annotator.

These are deliberately small, readable and genre-general (microdrama: betrayal,
revenge, romance, hidden identity). They are a fallback for when no LLM is
configured, and a cross-check when one is.
"""
from __future__ import annotations

import re

LEX: dict[str, list[str]] = {
    "physical": [
        r"slap\w*", r"strik\w*", r"struck", r"punch\w*", r"shov\w*", r"grab\w*", r"seiz\w*",
        r"drag\w*", r"hurl\w*", r"kick\w*", r"chok\w*", r"stab\w*", r"sword\w*", r"blade\w*",
        r"knife", r"dagger\w*", r"blood\w*", r"bleed\w*", r"wound\w*", r"collaps\w*",
        r"shatter\w*", r"explo\w*", r"blaz\w*", r"flame\w*", r"inferno", r"on fire", r"smoke",
        r"chain\w*", r"shackle\w*", r"whip\w*", r"riding crop", r"red-hot", r"iron", r"brand\w*",
        r"tackl\w*", r"slam\w*", r"lung\w*", r"attack\w*", r"overpower\w*", r"fight\w*",
        r"noose", r"gallows", r"thrown", r"throws", r"crash\w*", r"falls", r"lacerat\w*",
        r"thorn\w*", r"briar\w*", r"burn\w*",
    ],
    "threat": [
        r"kill\w*", r"\bdie\b", r"dies", r"death", r"dead", r"hang\b", r"hanged", r"hang you",
        r"gallows", r"execut\w*", r"arrest\w*", r"prison", r"dungeon\w*", r"gaol", r"witch\w*",
        r"poison\w*", r"arsenic", r"destroy\w*", r"ruin\w*", r"punish\w*", r"tortur\w*",
        r"starv\w*", r"burn you", r"you will pay", r"regret", r"warn\w*", r"threat\w*",
        r"\bguards?\b", r"seize her", r"take her",
    ],
    "reveal": [
        r"truth", r"secret\w*", r"reveal\w*", r"realiz\w*", r"recogni\w*", r"identity",
        r"who are you", r"who you really", r"really are", r"discover\w*", r"unmask\w*",
        r"confess\w*", r"proof", r"evidence", r"crest", r"signet", r"alive", r"presumed dead",
        r"you lied", r"it was you", r"the duke", r"my son", r"true heir", r"rightful",
        r"behind (it|this)", r"mastermind", r"framed",
    ],
    "identity": [
        r"identity", r"who are you", r"who you really", r"true (name|heir|duke)", r"crest",
        r"\bduke\b", r"duchess", r"my son", r"alive", r"in disguise", r"disguis\w*",
    ],
    "betrayal": [
        r"betray\w*", r"abandon\w*", r"disown\w*", r"cast (you|her) aside", r"changed my mind",
        r"you knew", r"liar", r"lied", r"traitor\w*", r"fram\w*", r"false testimony",
        r"behind my back", r"stripped of", r"cut off", r"left me",
    ],
    "romance": [
        r"\blove\w*", r"kiss\w*", r"embrac\w*", r"marry", r"married", r"marriage", r"\bwife\b",
        r"husband", r"\bbride\b", r"wedding", r"propos\w*", r"one knee", r"my heart",
        r"tender\w*", r"intima\w*", r"caress\w*", r"forehead", r"in his arms", r"in her arms",
    ],
    "confront": [
        r"how dare", r"\bdare\b", r"get out", r"enough", r"accus\w*", r"defian\w*", r"def(y|ies)",
        r"confront\w*", r"mock\w*", r"humiliat\w*", r"insult\w*", r"sneer\w*", r"glar\w*",
        r"shout\w*", r"scream\w*", r"yell\w*", r"\bstop\b", r"silence", r"on your knees",
        r"you will never", r"you'll never", r"beg\b", r"spits?", r"gloat\w*", r"taunt\w*",
        r"\bwhore\b", r"worthless", r"pathetic", r"filth\w*",
    ],
    "mystery": [
        r"mysterious", r"stranger", r"hooded", r"shadow\w*", r"cloak\w*", r"unknown", r"figure",
        r"masked", r"secret passage", r"who is (he|she|that)", r"who's there", r"someone",
        r"silhouette\w*",
    ],
    "rescue": [
        r"rescu\w*", r"\bsav(e|es|ed)\b", r"protect\w*", r"interven\w*", r"catch\w* (her|him)",
        r"shield\w*", r"carr(y|ies|ied) (her|him)", r"\bfree(s|d)?\b", r"unchain\w*", r"steps between",
    ],
    "proposal": [r"marry me", r"be my wife", r"propos\w*", r"one knee", r"kneel\w*"],
    "rejection": [r"\breject\w*", r"\brefus\w*", r"i will not", r"i won't", r"never again", r"\bno\.", r"\bno!"],
    "shock": [
        r"gasp\w*", r"shock\w*", r"stun\w*", r"freez\w*", r"froze", r"horrif\w*", r"stunned",
        r"jaw drops", r"eyes widen\w*", r"wide-eyed", r"disbelief", r"pale", r"aghast",
        r"bursts? (open|in)", r"slams? open", r"suddenly", r"without warning",
    ],
    "interrupt": [
        r"bursts? (open|in|through)", r"doors? (fly|flies|slam\w*|swing\w*) open", r"enters?\b",
        r"appears?\b", r"steps? (in|out|forward)", r"arriv\w*", r"footsteps", r"knock\w*",
        r"interrupt\w*", r"a voice", r"freez\w*",
    ],
    "resolution": [
        r"\bsmil\w*", r"relie\w*", r"\blaugh\w*", r"peace\w*", r"\bsleep\w*", r"thank you",
        r"it's over", r"it is over", r"\bsafe\b", r"forgiv\w*", r"\bheal\w*", r"\bcalm\w*",
        r"\bcontent\w*", r"\bwarm\w*", r"together", r"\bhome\b", r"apprehend\w*", r"proclaim\w*",
        r"exonerat\w*", r"cured", r"recover\w*",
    ],
    "emotion": [
        r"tears?", r"\bweep\w*", r"sob\w*", r"trembl\w*", r"shak(e|es|ing)", r"rage", r"furious",
        r"fury", r"anguish\w*", r"grief", r"despair\w*", r"heartbr\w*", r"devastat\w*",
        r"crying", r"cries", r"anger", r"agon\w*", r"terror", r"terrified", r"panic\w*",
    ],
    "backref": [
        r"\bagain\b", r"remember", r"after what", r"last night", r"what you did", r"as i said",
        r"you promised", r"like before", r"once more", r"that night", r"all along",
    ],
    "context": [
        r"\bsister\b", r"\bhusband\b", r"\bwife\b", r"\bmother\b", r"\bfather\b", r"\bson\b",
        r"\bdaughter\b", r"\bduke\b", r"\bduchess\b", r"stepmother", r"fianc\w*", r"betrothed",
        r"\bmaid\b", r"\bbrother\b", r"family",
    ],
}

# delivery cue words -> intensity 0..3
CUE_INTENSITY: dict[str, float] = {}
for w in ("screaming scream shrieking shouting shout roaring roar furious enraged livid hysterical "
          "frantic desperate sobbing sobs wailing outraged murderous vicious savage feral "
          "terrified horrified crying anguished devastated broken choking gasping").split():
    CUE_INTENSITY[w] = 3.0
for w in ("venomous menacing threatening mocking gloating cruel icy cold accusing piercing betrayed "
          "defiant commanding sneering spiteful contemptuous trembling shaken pleading heartbroken "
          "unyielding seething bitter hostile disgusted fierce urgent alarmed shocked stunned "
          "panicked tearful vengeful dangerous callous taunting smug triumphant scornful").split():
    CUE_INTENSITY[w] = 2.0
for w in ("firm grave stern tense resolute solemn sharp low hushed whispering whisper "
          "dismissive blunt urgent wary suspicious strained scheming sly calculating").split():
    CUE_INTENSITY.setdefault(w, 1.0)

CAMERA_DRAMA = [
    r"extreme close-up", r"slow[- ]motion", r"crash zoom", r"snap zoom", r"whip pan",
    r"low[- ]angle", r"dutch", r"handheld", r"push(es|ing)? in", r"rack focus", r"insert",
    r"close-up on .*(face|eyes|lips|hand)",
]
ESTABLISHING = [
    r"\bwide (establishing )?shot of the (exterior|estate|manor|cottage|village|forest|castle)",
    r"establishing", r"exterior of", r"aerial", r"drone", r"\bvista\b", r"landscape",
]

_COMPILED = {k: re.compile(r"(?i)\b(?:" + "|".join(v) + r")") for k, v in LEX.items()}
_CAM = re.compile(r"(?i)(?:" + "|".join(CAMERA_DRAMA) + r")")
_EST = re.compile(r"(?i)(?:" + "|".join(ESTABLISHING) + r")")


def hits(category: str, text: str) -> int:
    return len(_COMPILED[category].findall(text))


def first_hit(category: str, text: str) -> str | None:
    m = _COMPILED[category].search(text)
    return m.group(0) if m else None


def camera_drama(text: str) -> int:
    return len(_CAM.findall(text))


def establishing(text: str) -> bool:
    return bool(_EST.search(text))


def cue_intensity(cues: list[str]) -> float:
    best = 0.0
    for cue in cues:
        for w in re.findall(r"[a-z]+", cue.lower()):
            best = max(best, CUE_INTENSITY.get(w, 0.0))
    return best
