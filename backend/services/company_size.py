"""
Classify companies as small / mid / large — locally, at zero API cost.

Signals, in priority order:
  1. A curated list of well-known Indian + global employers (brand awareness).
  2. Name heuristics (Pvt Ltd / Technologies / Consulting …).
  3. Default to "mid" when unknown, so nothing is wrongly excluded.

"Large"  = 5,000+ employees, enterprise/MNC, household-name brands
"Mid"    = ~200–5,000, funded growth-stage, established but not household
"Small"  = <200, startups, agencies, small consultancies
"""

_LARGE = {
    # Global tech & MNCs hiring in India
    "google", "microsoft", "amazon", "meta", "facebook", "apple", "netflix", "uber",
    "adobe", "oracle", "sap", "salesforce", "ibm", "intel", "nvidia", "qualcomm",
    "cisco", "dell", "hp", "vmware", "paypal", "visa", "mastercard", "walmart",
    "goldman sachs", "morgan stanley", "jpmorgan", "jp morgan", "barclays", "hsbc",
    "deutsche bank", "wells fargo", "citi", "citibank", "american express",
    # Indian IT & conglomerates
    "tcs", "tata consultancy", "infosys", "wipro", "hcl", "tech mahindra", "cognizant",
    "accenture", "capgemini", "deloitte", "ey", "ernst", "kpmg", "pwc", "mckinsey",
    "bcg", "bain", "reliance", "adani", "tata", "mahindra", "birla", "godrej", "itc",
    "l&t", "larsen", "bharti", "airtel", "jio", "vodafone",
    # Indian consumer/unicorns at scale
    "flipkart", "myntra", "swiggy", "zomato", "paytm", "phonepe", "ola", "oyo",
    "byju", "unacademy", "nykaa", "meesho", "zerodha", "policybazaar", "delhivery",
    "bigbasket", "dmart", "hdfc", "icici", "axis bank", "sbi", "kotak", "bajaj",
    "asian paints", "hindustan unilever", "hul", "nestle", "itc limited", "britannia",
    "maruti", "hyundai", "toyota", "honda", "bosch", "siemens", "abb", "schneider",
    "anker", "samsung", "lg", "sony", "philips", "whirlpool", "haier",
    "jenoptik", "electrolux", "mars", "pepsico", "coca cola", "p&g", "procter",
}

_MID = {
    "razorpay", "cred", "groww", "upstox", "zepto", "dunzo", "urban company", "lenskart",
    "cars24", "spinny", "licious", "purplle", "mamaearth", "boat", "noise", "wakefit",
    "freshworks", "zoho", "chargebee", "postman", "browserstack", "hasura", "clevertap",
    "moengage", "webengage", "innovaccer", "darwinbox", "keka", "zeta", "juspay",
    "instahyre", "naukri", "info edge", "quikr", "olx", "practo", "pharmeasy", "1mg",
    "thoughtworks", "publicis", "sapient", "mindtree", "mphasis", "ltimindtree",
    "persistent", "zensar", "birlasoft", "coforge", "hexaware", "cyient",
    "tiger analytics", "fractal", "mu sigma", "latentview", "sigmoid", "quantiphi",
}

_SMALL_HINTS = ("startup", "labs", "studio", "agency", "consultancy", "consulting",
                "solutions", "ventures", "partners", "associates", "enterprises")


def classify_company(name: str) -> str:
    """Returns 'small' | 'mid' | 'large'."""
    n = (name or "").strip().lower()
    if not n:
        return "mid"
    for key in _LARGE:
        if key in n:
            return "large"
    for key in _MID:
        if key in n:
            return "mid"
    # Heuristics for the long tail
    if any(h in n for h in _SMALL_HINTS):
        return "small"
    if any(t in n for t in (" ltd", " limited", " inc", " corporation", " group", " industries")):
        return "mid"
    # Unknown brand with a plain name → most likely a smaller company
    return "small"


def matches_company_type(name: str, wanted: str) -> bool:
    """wanted: '' | 'small' | 'mid' | 'large' (comma-separated allowed)."""
    if not wanted:
        return True
    want = {w.strip().lower() for w in wanted.split(",") if w.strip()}
    if not want:
        return True
    return classify_company(name) in want
