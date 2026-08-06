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


# Opt-out toggle (Language Rules page), DEFAULTS TO ON — unlike the
# Catalan add-on below, which defaults off. The bare "Spanish (es)"
# language code/name is ambiguous between European (Castilian/Peninsular)
# and Latin American Spanish, and a real translation confirmed live
# defaulted to Latin American colloquial phrasing ("¿Qué anduvo Missy
# ahora?" — a loose "andar" construction, not standard/Peninsular usage)
# with no way to have requested otherwise from the bare code. Bazarr's
# own language codes don't distinguish es-ES from es-419/es-MX, so this
# is the only place that distinction can be enforced. Users who actually
# want Latin American Spanish output can flip the toggle off.
EUROPEAN_SPANISH_ADDON = (
    " Use Peninsular Spanish from Spain (Castilian Spanish) — vocabulary, "
    "verb conjugations (vosotros, not ustedes, for informal plural you), "
    "and idioms as used in Spain, NOT Latin American Spanish or any other "
    "regional dialect."
)


# Optional add-on, appended only when translating INTO Catalan and the
# corresponding Language Rules toggle is on. Deliberately doesn't hardcode
# a fixed phrase list — confirmed live that DeepSeek V4 Flash already
# recognizes this specific cultural reference (TV3's Bola de Drac Z dub)
# and produces natural, in-character adaptations from the style
# description alone (e.g. "Maleïda bèstia... tros d'inútil!" for "you
# stupid idiot... worthless piece of garbage"), without needing a curated
# vocabulary baked into the prompt.
CATALAN_VEGETA_INSULTS_ADDON = (
    " For any insult, swear word, or contemptuous expression in the "
    "source dialogue, do NOT translate it literally — adapt it into the "
    "proud, colorful, larger-than-life insult style of Vegeta's Catalan "
    "dub (TV3's Bola de Drac Z), reflecting his characteristic tone of "
    "pride and superiority. Match the intensity and intent of the "
    "ORIGINAL insult, not just its general category — a mild jab (e.g. "
    "\"idiot\") should get a comparatively milder Vegeta-style dismissal, "
    "while a harsh, degrading insult should get one of his more severe, "
    "contemptuous ones. Consider who is speaking to whom and why, so the "
    "chosen insult fits the scene's context, not a random pick from the "
    "same style. Keep the rest of the translation natural and accurate; "
    "this rule applies only to insults/profanity."
)


def build_system_prompt(
    source_lang_code: str,
    target_lang_code: str,
    catalan_vegeta_insults: bool = False,
    european_spanish: bool = True,
) -> str:
    prompt = SYSTEM_PROMPT.format(
        source_lang=language_name(source_lang_code),
        source_lang_code=source_lang_code,
        target_lang=language_name(target_lang_code),
        target_lang_code=target_lang_code,
    )
    if catalan_vegeta_insults and target_lang_code == "ca":
        prompt += CATALAN_VEGETA_INSULTS_ADDON
    if european_spanish and target_lang_code == "es":
        prompt += EUROPEAN_SPANISH_ADDON
    return prompt


def build_user_prompt(dialogue_text: str) -> str:
    return dialogue_text
