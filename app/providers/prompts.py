from app.providers.languages import language_name

# Spelling out full language names (not bare ISO codes) matters — a live run
# asking to translate "from en to it" came back entirely in German with a
# small local model, most plausibly from misreading the bare codes. Full
# names removed the ambiguity.
SYSTEM_PROMPT = (
    "You are a professional subtitle translator working on licensed film "
    "and television content. You will be given subtitle dialogue lines, "
    "each preceded by its numeric index. Translate ONLY the dialogue text "
    "from {source_lang} ({source_lang_code}) to {target_lang} "
    "({target_lang_code}). The output MUST be in {target_lang} — do not use "
    "any other language. "
    "Preserve the exact numeric index for every line, one per output block, "
    "in the same '<index>\\n<translated text>' format separated by blank "
    "lines. Do not add, remove, merge, or reorder lines. Even when two or "
    "more consecutive lines look like one continuous sentence split across "
    "cues, translate and output them SEPARATELY, one per original index — "
    "never combine them into a single output block. Every input index MUST "
    "have exactly one corresponding output block, in the same order. Do "
    "not add commentary, explanations, or any text outside the "
    "index/translation pairs. Keep line breaks within a single cue's text "
    "if present. "
    "Translate profanity and vulgar language directly and faithfully — use "
    "the real equivalent swear word or vulgar expression in {target_lang}, "
    "matching the source's intensity. Do not soften, censor, or replace it "
    "with a milder or more polite alternative, UNLESS a more specific "
    "instruction below asks for a different style of adaptation instead — "
    "in that case, follow that instruction."
)


# A bare ISO 639-1 code/language name is ambiguous whenever a language has
# more than one widely-used regional standard — confirmed live for Spanish:
# a real translation defaulted to Latin American colloquial phrasing
# ("¿Qué anduvo Missy ahora?") with no way to have requested otherwise from
# the bare code, since Bazarr's own language codes don't distinguish es-ES
# from es-MX/es-AR/es-419. Each entry below is ONE language's set of
# regional variants, keyed by a short variant id, each with a
# (label, prompt addon). Coverage is deliberately limited to each
# language's clearly-dominant, well-known regional standards, not an
# attempt to enumerate every possible dialect of every language.
LANGUAGE_VARIANTS: dict[str, dict[str, tuple[str, str]]] = {
    "es": {
        "es-ES": (
            "European (Castilian) Spain",
            " Use Peninsular Spanish from Spain (Castilian Spanish) — "
            "vocabulary, verb conjugations (vosotros, not ustedes, for "
            "informal plural you), and idioms as used in Spain, NOT Latin "
            "American Spanish or any other regional dialect.",
        ),
        "es-MX": (
            "Mexican",
            " Use Mexican Spanish — vocabulary, verb conjugations "
            "(ustedes, not vosotros, for informal plural you), and idioms "
            "as used in Mexico, NOT Peninsular Spanish from Spain, "
            "Argentine Spanish, or any other regional dialect.",
        ),
        "es-AR": (
            "Argentine",
            " Use Argentine Spanish (Rioplatense) — vocabulary, voseo verb "
            "conjugations (vos, not tú, for informal singular you), and "
            "idioms as used in Argentina, NOT Peninsular Spanish from "
            "Spain, Mexican Spanish, or any other regional dialect.",
        ),
        "es-419": (
            "Latin American (generic)",
            " Use general neutral Latin American Spanish — vocabulary and "
            "idioms (ustedes, not vosotros, for informal plural you) not "
            "tied to any single Latin American country, NOT Peninsular/"
            "Castilian Spanish from Spain.",
        ),
    },
    "pt": {
        "pt-PT": (
            "European Portugal",
            " Use European Portuguese from Portugal — vocabulary, verb "
            "conjugations, and idioms as used in Portugal, NOT Brazilian "
            "Portuguese or any other regional dialect.",
        ),
        "pt-BR": (
            "Brazilian",
            " Use Brazilian Portuguese — vocabulary, verb conjugations, "
            "and idioms as used in Brazil, NOT European Portuguese from "
            "Portugal or any other regional dialect.",
        ),
    },
    "en": {
        "en-US": (
            "American",
            " Use American English — spelling (e.g. \"color\" not "
            "\"colour\"), vocabulary (e.g. \"truck\" not \"lorry\"), and "
            "idioms as used in the United States, NOT British/UK English "
            "or any other regional dialect.",
        ),
        "en-GB": (
            "British",
            " Use British English — spelling (e.g. \"colour\" not "
            "\"color\"), vocabulary (e.g. \"lorry\" not \"truck\"), and "
            "idioms as used in the United Kingdom, NOT American English or "
            "any other regional dialect.",
        ),
    },
    "fr": {
        "fr-FR": (
            "France",
            " Use Metropolitan French from France — vocabulary, grammar, "
            "and idioms as used in France, NOT Québécois, Belgian, Swiss, "
            "or any other regional dialect.",
        ),
        "fr-CA": (
            "Québécois",
            " Use Québécois (Canadian) French — vocabulary, grammar, and "
            "idioms as used in Québec, NOT Metropolitan French from "
            "France, Belgian, Swiss, or any other regional dialect.",
        ),
        "fr-BE": (
            "Belgian",
            " Use Belgian French — vocabulary as used in Belgium "
            "(including numbers like \"septante\"/\"nonante\" instead of "
            "\"soixante-dix\"/\"quatre-vingt-dix\"), NOT Metropolitan "
            "French from France, Québécois, Swiss, or any other regional "
            "dialect.",
        ),
        "fr-CH": (
            "Swiss",
            " Use Swiss French — vocabulary as used in French-speaking "
            "Switzerland (including numbers like \"septante\"/\"huitante\" "
            "instead of \"soixante-dix\"/\"quatre-vingts\"), NOT "
            "Metropolitan French from France, Québécois, Belgian, or any "
            "other regional dialect.",
        ),
    },
    "zh": {
        "zh-Hans": (
            "Simplified (Mainland)",
            " Use Simplified Chinese characters and Mainland China "
            "vocabulary/phrasing, NOT Traditional Chinese characters or "
            "Taiwan/Hong Kong regional vocabulary.",
        ),
        "zh-Hant": (
            "Traditional (Taiwan/HK)",
            " Use Traditional Chinese characters and Taiwan/Hong Kong "
            "vocabulary/phrasing, NOT Simplified Chinese characters or "
            "Mainland China regional vocabulary.",
        ),
    },
}

# Each language's safer/more expected default variant — matches how
# European Spanish previously defaulted ON. English is the one case where
# the language's "home" country (UK) is NOT the default, since American
# English is the far more common expected target for this kind of use case.
DEFAULT_LANGUAGE_VARIANTS: dict[str, str] = {
    "es": "es-ES",
    "pt": "pt-PT",
    "en": "en-US",
    "fr": "fr-FR",
    "zh": "zh-Hans",
}


# Optional add-on, appended only when translating INTO Catalan and the
# corresponding Language Rules toggle is on. Deliberately doesn't hardcode
# a fixed phrase list — confirmed live that DeepSeek V4 Flash already
# recognizes this specific cultural reference (TV3's Bola de Drac Z dub)
# and produces natural, in-character adaptations from the style
# description alone (e.g. "Maleïda bèstia... tros d'inútil!" for "you
# stupid idiot... worthless piece of garbage"), without needing a curated
# vocabulary baked into the prompt.
CATALAN_VEGETA_INSULTS_ADDON = (
    " Override the earlier instruction to translate profanity directly: "
    "for any insult, swear word, or contemptuous expression in the "
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
    language_variants: dict[str, str] | None = None,
) -> str:
    """language_variants maps a language code (e.g. "es") to the chosen
    variant key (e.g. "es-419") — see LANGUAGE_VARIANTS above. Any language
    not present in the dict, or an unrecognized variant key, falls back to
    that language's DEFAULT_LANGUAGE_VARIANTS entry (or no addon at all for
    languages with no variants defined)."""
    prompt = SYSTEM_PROMPT.format(
        source_lang=language_name(source_lang_code),
        source_lang_code=source_lang_code,
        target_lang=language_name(target_lang_code),
        target_lang_code=target_lang_code,
    )
    if catalan_vegeta_insults and target_lang_code == "ca":
        prompt += CATALAN_VEGETA_INSULTS_ADDON

    variants = LANGUAGE_VARIANTS.get(target_lang_code)
    if variants:
        chosen = (language_variants or {}).get(target_lang_code) or DEFAULT_LANGUAGE_VARIANTS.get(
            target_lang_code
        )
        addon = variants.get(chosen)
        if addon is None:
            addon = variants[DEFAULT_LANGUAGE_VARIANTS[target_lang_code]]
        prompt += addon[1]
    return prompt


def build_user_prompt(dialogue_text: str) -> str:
    return dialogue_text
