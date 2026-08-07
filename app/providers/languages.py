"""ISO 639-1 two-letter code -> English language name, used to make LLM
prompts unambiguous. Bare codes like "en"/"it" are a real failure mode with
smaller models — a live run asking to translate "from en to it" came back
in German instead, most plausibly because the model misread the bare codes.
Spelling out full names in the prompt removes that ambiguity.

This table is ISO 639-1 only. Bazarr also uses non-standard codes of its
own (e.g. "pb" for Brazilian Portuguese) that aren't ISO codes and aren't
listed here — those are resolved at runtime via refresh_bazarr_names()
instead, since guessing/hardcoding Bazarr-specific codes here would drift
from whatever a given Bazarr install actually calls them."""

ISO_639_1_NAMES: dict[str, str] = {
    "aa": "Afar", "ab": "Abkhazian", "af": "Afrikaans", "ak": "Akan",
    "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "an": "Aragonese",
    "hy": "Armenian", "as": "Assamese", "av": "Avaric", "ae": "Avestan",
    "ay": "Aymara", "az": "Azerbaijani", "bm": "Bambara", "ba": "Bashkir",
    "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bh": "Bihari",
    "bi": "Bislama", "bs": "Bosnian", "br": "Breton", "bg": "Bulgarian",
    "my": "Burmese", "ca": "Catalan", "ch": "Chamorro", "ce": "Chechen",
    "ny": "Chichewa", "zh": "Chinese", "cv": "Chuvash", "kw": "Cornish",
    "co": "Corsican", "cr": "Cree", "hr": "Croatian", "cs": "Czech",
    "da": "Danish", "dv": "Divehi", "nl": "Dutch", "dz": "Dzongkha",
    "en": "English", "eo": "Esperanto", "et": "Estonian", "ee": "Ewe",
    "fo": "Faroese", "fj": "Fijian", "fi": "Finnish", "fr": "French",
    "ff": "Fulah", "gl": "Galician", "ka": "Georgian", "de": "German",
    "el": "Greek", "gn": "Guarani", "gu": "Gujarati", "ht": "Haitian Creole",
    "ha": "Hausa", "he": "Hebrew", "hz": "Herero", "hi": "Hindi",
    "ho": "Hiri Motu", "hu": "Hungarian", "ia": "Interlingua", "id": "Indonesian",
    "ie": "Interlingue", "ga": "Irish", "ig": "Igbo", "ik": "Inupiaq",  
    "io": "Ido", "is": "Icelandic", "it": "Italian", "iu": "Inuktitut",
    "ja": "Japanese", "jv": "Javanese", "kl": "Kalaallisut", "kn": "Kannada",
    "kr": "Kanuri", "ks": "Kashmiri", "kk": "Kazakh", "km": "Khmer",
    "ki": "Kikuyu", "rw": "Kinyarwanda", "ky": "Kyrgyz", "kv": "Komi",
    "kg": "Kongo", "ko": "Korean", "ku": "Kurdish", "kj": "Kuanyama",
    "la": "Latin", "lb": "Luxembourgish", "lg": "Ganda", "li": "Limburgish",
    "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian", "lu": "Luba-Katanga",
    "lv": "Latvian", "gv": "Manx", "mk": "Macedonian", "mg": "Malagasy",
    "ms": "Malay", "ml": "Malayalam", "mt": "Maltese", "mi": "Maori",
    "mr": "Marathi", "mh": "Marshallese", "mn": "Mongolian", "na": "Nauru",
    "nv": "Navajo", "nd": "North Ndebele", "ne": "Nepali", "ng": "Ndonga",
    "nb": "Norwegian Bokmål", "nn": "Norwegian Nynorsk", "no": "Norwegian",
    "ii": "Sichuan Yi", "nr": "South Ndebele", "oc": "Occitan", "oj": "Ojibwe",
    "cu": "Church Slavic", "om": "Oromo", "or": "Oriya", "os": "Ossetian",
    "pa": "Punjabi", "pi": "Pali", "fa": "Persian", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "qu": "Quechua", "rm": "Romansh",
    "rn": "Rundi", "ro": "Romanian", "ru": "Russian", "sa": "Sanskrit",
    "sc": "Sardinian", "sd": "Sindhi", "se": "Northern Sami", "sm": "Samoan",
    "sg": "Sango", "sr": "Serbian", "gd": "Scottish Gaelic", "sn": "Shona",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "so": "Somali",
    "st": "Southern Sotho", "es": "Spanish", "su": "Sundanese", "sw": "Swahili",
    "ss": "Swati", "sv": "Swedish", "ta": "Tamil", "te": "Telugu",
    "tg": "Tajik", "th": "Thai", "ti": "Tigrinya", "bo": "Tibetan",
    "tk": "Turkmen", "tl": "Tagalog", "tn": "Tswana", "to": "Tongan",
    "tr": "Turkish", "ts": "Tsonga", "tt": "Tatar", "tw": "Twi",
    "ty": "Tahitian", "ug": "Uyghur", "uk": "Ukrainian", "ur": "Urdu",
    "uz": "Uzbek", "ve": "Venda", "vi": "Vietnamese", "vo": "Volapük",
    "wa": "Walloon", "cy": "Welsh", "wo": "Wolof", "fy": "Western Frisian",
    "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "za": "Zhuang",
    "zu": "Zulu",
}


# Populated once at startup from Bazarr's own GET /api/system/languages
# (see refresh_bazarr_names below) — takes priority over ISO_639_1_NAMES
# since it reflects exactly what this Bazarr instance calls each code,
# including non-standard ones (like "pb") the static ISO table can't cover.
_bazarr_names: dict[str, str] = {}


def language_name(code: str) -> str:
    """Returns the full English name for a language code, or the code
    itself uppercased if unknown — never raises, since an unrecognized
    code shouldn't block translation, it just loses the disambiguation
    benefit for that one language."""
    code = code.lower()
    return _bazarr_names.get(code) or ISO_639_1_NAMES.get(code, code.upper())


async def refresh_bazarr_names(client) -> None:
    """Fetches Bazarr's known-language list and caches it as the preferred
    source for language_name(). Best-effort: if Bazarr isn't reachable or
    configured yet, leaves the existing cache (or the ISO fallback) in
    place rather than raising, since prompt-building must never break on
    this."""
    try:
        rows = await client.get_languages()
    except Exception:
        return
    _bazarr_names.clear()
    for row in rows:
        code = row.get("code2")
        name = row.get("name")
        if code and name:
            _bazarr_names[code.lower()] = name
