from app.providers.languages import language_name

# Spelling out full language names (not bare ISO codes) matters — a live run
# asking to translate "from en to it" came back entirely in German with a
# small local model, most plausibly from misreading the bare codes. Full
# names removed the ambiguity.
SYSTEM_PROMPT = (
    "You are a professional subtitle translator. You will be given subtitle "
    "dialogue lines, each preceded by its numeric index. Translate ONLY the "
    "dialogue text from {source_lang} ({source_lang_code}) to {target_lang} "
    "({target_lang_code}). The output MUST be in {target_lang} — do not use "
    "any other language. "
    "Preserve the exact numeric index for every line, one per output block, "
    "in the same '<index>\\n<translated text>' format separated by blank "
    "lines. Do not add, remove, merge, or reorder lines. Do not add "
    "commentary, explanations, or any text outside the index/translation "
    "pairs. Keep line breaks within a single cue's text if present."
)


def build_system_prompt(source_lang_code: str, target_lang_code: str) -> str:
    return SYSTEM_PROMPT.format(
        source_lang=language_name(source_lang_code),
        source_lang_code=source_lang_code,
        target_lang=language_name(target_lang_code),
        target_lang_code=target_lang_code,
    )


def build_user_prompt(dialogue_text: str) -> str:
    return dialogue_text
