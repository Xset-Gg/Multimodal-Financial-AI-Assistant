"""Rule-based merchant cleanup and categorization (deterministic, explainable)."""

from __future__ import annotations

import re

CATEGORIES = [
    "Dining",
    "Groceries",
    "Transport",
    "Travel",
    "Shopping",
    "Entertainment",
    "Utilities & Telecom",
    "Health",
    "Education",
    "Fees & Interest",
    "Payments & Credits",
    "Transfers",
    "Uncategorized",
]

# keyword (matched on word boundaries against the normalized description) -> category
_RULES: list[tuple[str, list[str]]] = [
    ("Payments & Credits", [
        "payment received", "payment - thank you", "payment thank you", "thank you for your payment",
        "autopay", "direct debit payment", "refund", "reversal", "cashback", "cash back", "credit adjustment",
    ]),
    ("Fees & Interest", [
        "annual fee", "late fee", "late payment fee", "interest charge", "finance charge", "interest",
        "foreign transaction fee", "fx fee", "service charge", "overlimit fee", "cash advance fee", "vat on fee",
    ]),
    ("Dining", [
        "restaurant", "cafe", "café", "coffee", "starbucks", "costa", "tim hortons", "mcdonald", "kfc",
        "burger", "pizza", "domino", "subway", "shake shack", "grill", "kitchen", "bistro", "diner",
        "talabat", "deliveroo", "uber eats", "ubereats", "snoonu", "zomato", "doordash", "grubhub",
        "bakery", "sushi", "shawarma", "eatery", "steakhouse", "nandos", "hardee", "chili's", "applebee",
        "dunkin", "baskin", "tea house", "karak", "cheesecake factory", "p.f. chang",
    ]),
    ("Groceries", [
        "supermarket", "hypermarket", "grocery", "carrefour", "lulu", "monoprix", "al meera", "almeera",
        "spinneys", "waitrose", "tesco", "sainsbury", "whole foods", "trader joe", "kroger", "safeway",
        "aldi", "lidl", "family food", "market", "megamart", "costco",
    ]),
    ("Transport", [
        "uber", "careem", "lyft", "karwa", "taxi", "metro", "parking", "fuel", "petrol", "woqod",
        "shell", "esso", "chevron", "mobil", "adnoc", "toll", "bolt", "rail", "transit", "bus ",
    ]),
    ("Travel", [
        "airways", "airlines", "air ", "qatar airways", "emirates", "etihad", "flydubai", "ryanair",
        "easyjet", "hotel", "marriott", "hilton", "hyatt", "sheraton", "airbnb", "booking.com", "expedia",
        "agoda", "travel", "duty free", "rental car", "hertz", "avis", "sixt",
    ]),
    ("Entertainment", [
        "netflix", "spotify", "apple music", "youtube", "disney", "cinema", "vox", "novo", "steam",
        "playstation", "xbox", "nintendo", "theatre", "theater", "ticket", "osn", "shahid", "anghami",
        "bowling", "concert",
    ]),
    ("Utilities & Telecom", [
        "ooredoo", "vodafone", "kahramaa", "etisalat", "du ", "stc", "electric", "water", "utility",
        "internet", "broadband", "mobile", "telecom", "verizon", "at&t", "t-mobile",
    ]),
    ("Health", [
        "pharmacy", "clinic", "hospital", "medical", "dental", "wellcare", "boots", "cvs", "walgreens",
        "optic", "health", "gym", "fitness", "physio",
    ]),
    ("Education", ["school", "university", "college", "coursera", "udemy", "tuition", "academy", "books"]),
    ("Transfers", ["transfer", "remittance", "western union", "wise", "paypal *transfer", "cash withdrawal", "atm"]),
    ("Shopping", [
        "amazon", "amzn", "noon", "ikea", "zara", "h&m", "apple store", "apple.com", "virgin megastore",
        "jarir", "extra", "sharaf", "mall", "store", "shop", "boutique", "centrepoint", "max fashion",
        "ebay", "aliexpress", "shein", "nike", "adidas", "sephora", "home centre", "decathlon", "electronics",
    ]),
]

_COMPILED = [
    (category, [re.compile(r"(?<![a-z0-9])" + re.escape(k.strip()) + r"(?![a-z0-9])") for k in keywords])
    for category, keywords in _RULES
]

_NOISE = re.compile(
    r"""
    \b(?:pos|purchase|purch|card\s*(?:no\.?|ending)?\s*\*?\d{2,4}|visa|mastercard|mc|debit|credit|
    ecom|e-com|online|contactless|intl|international|trx|txn|ref(?:erence)?\s*[:#]?\s*\w+|auth\s*\w+)\b
    """,
    re.I | re.X,
)
_PREFIXES = re.compile(r"^(?:SQ|SP|TST|PAYPAL|PP|GOOGLE|AMZN MKTP)\s*\*\s*", re.I)
_COUNTRY_TAIL = re.compile(
    r"\s+(?:QA|QAT|AE|ARE|UAE|SA|SAU|KW|BH|OM|GB|GBR|UK|US|USA|FR|FRA|TR|TUR|SG|SGP|IN|IND|DE|DEU|NL|NLD|IE|IRL|"
    r"LU|LUX|SE|SWE|ES|ESP|IT|ITA|CH|CHE|CA|CAN|AU|AUS|JP|JPN|CN|CHN|HK|EG|JO|LB|PK|MY|TH|"
    # US state codes (US statements print "CITY ST")
    r"AL|AK|AZ|AR|CO|CT|DE|FL|GA|HI|ID|IL|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|"
    r"RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY|DC)\s*$",
    re.I,
)
_KNOWN_PLACES = re.compile(
    r"\s+(?:DOHA|LUSAIL|AL\s+WAKRAH|AL\s+RAYYAN|AL\s+GHARRAFA|AL\s+KHOR|WEST\s+BAY|THE\s+PEARL|DUBAI|ABU\s+DHABI|"
    r"SHARJAH|RIYADH|JEDDAH|KUWAIT\s+CITY|MANAMA|MUSCAT|LONDON|NEW\s+YORK|LOS\s+ANGELES|LOS\s+GATOS|SAN\s+FRANCISCO|"
    r"SAN\s+JOSE|SEATTLE|CUPERTINO|MOUNTAIN\s+VIEW|PARIS|ISTANBUL|SINGAPORE|AMSTERDAM|DUBLIN|CORK|STOCKHOLM|"
    r"LUXEMBOURG|BERLIN|MUNICH|MADRID|BARCELONA|ROME|MILAN|ZURICH|GENEVA|TORONTO|SYDNEY|TOKYO|MUMBAI|DELHI|"
    r"BANGALORE|CAIRO|AMMAN|BEIRUT|KARACHI|LAHORE|KUALA\s+LUMPUR|BANGKOK|HONG\s+KONG|QATAR)\s*$",
    re.I,
)
# Words that are part of a business name, never a trailing city.
_BUSINESS_WORDS = {
    "restaurant", "cafe", "coffee", "mall", "store", "shop", "market", "supermarket", "hypermarket", "center",
    "centre", "hotel", "pharmacy", "clinic", "station", "delivery", "bill", "payment", "fee", "charge", "taxi",
    "cinemas", "cinema", "airways", "airlines", "bakery", "kitchen", "grill", "food", "trip", "online", "services",
}
_KEEP_UPPER_SHORT = re.compile(r"^[A-Z&]{2,3}$")
_KNOWN_ACRONYMS = {"OSN", "DHL", "UPS", "ATM", "IKEA", "BMW", "AT&T", "USA", "UAE", "QNB", "HSBC", "KFC", "H&M"}


def _smart_case(word: str) -> str:
    if not word.isupper():
        return word
    if word in _KNOWN_ACRONYMS or (_KEEP_UPPER_SHORT.fullmatch(word) and not re.search(r"[AEIOU]", word)):
        return word  # likely an acronym: KFC, H&M, BP (short and vowel-less)
    if "." in word:
        return word.lower().capitalize()  # AMAZON.COM -> Amazon.com
    return word.capitalize()


def clean_merchant(description: str) -> str | None:
    """Best-effort merchant name from a raw description line. Returns None if nothing usable remains."""
    text = re.sub(r"\s+", " ", description or "").strip()
    text = _PREFIXES.sub("", text)
    text = re.sub(r"\*\s*(?=[A-Z0-9]*\d)[A-Z0-9]{2,12}\b", " ", text, flags=re.I)  # AMAZON.COM*MK12 -> AMAZON.COM
    text = text.replace("*", " ")
    text = _NOISE.sub(" ", text)
    text = re.sub(r"\b\d{5,}\b", " ", text)  # long reference numbers
    text = re.sub(r"#\s*\d*", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -,:;/")
    had_country = bool(_COUNTRY_TAIL.search(text))
    text = _COUNTRY_TAIL.sub("", text).strip(" -,:;/")
    before = text
    text = _KNOWN_PLACES.sub("", text).strip(" -,:;/")
    words = text.split(" ")
    if had_country and text == before and len(words) >= 2 and words[-1].lower() not in _BUSINESS_WORDS \
            and not re.search(r"[\d.]", words[-1]):
        # A country code was printed, so the word before it is most likely the city.
        text = " ".join(words[:-1])
    text = re.sub(r"\s+#?\d{3,}$", "", text)  # trailing store numbers: "SHELL OIL 5744"
    text = re.sub(r"\s+(?:OF|AT|IN|THE|-)\s*$", "", text, flags=re.I).strip(" -,:;/")
    if len(re.sub(r"[^A-Za-z]", "", text)) < 2:
        return None
    return " ".join(_smart_case(w) for w in text.split(" "))[:120]


def normalize_merchant_key(merchant: str | None) -> str | None:
    if not merchant:
        return None
    key = re.sub(r"[^A-Z0-9]+", " ", merchant.upper()).strip()
    return key or None


def categorize(description: str, merchant: str | None, txn_type: str) -> tuple[str, bool]:
    """Return (category, matched). Unmatched debits are 'Uncategorized'."""
    haystack = f" {(merchant or '').lower()} {description.lower()} "
    for category, patterns in _COMPILED:
        if any(p.search(haystack) for p in patterns):
            return category, True
    if txn_type == "credit":
        return "Payments & Credits", False
    return "Uncategorized", False
