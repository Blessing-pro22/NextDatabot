"""
Default bundles, used only to seed the database the first time the bot
runs. After that, bundles live entirely in the `bundles` table (see db.py)
and are fully manageable from the admin panel (/admin -> Bundles):
add a new size, edit its price, or remove one - no redeploy needed.
"""

DEFAULT_BUNDLES = {
    "MTN" : {
    1000: {"label": "1 GB", "price_ghs": 5.20},
    2000: {"label": "2 GB", "price_ghs": 10.20},
    3000: {"label": "3 GB", "price_ghs": 15.20},
    4000: {"label": "4 GB", "price_ghs": 21.20},
    5000: {"label": "5 GB", "price_ghs": 25.20},
    6000: {"label": "6 GB", "price_ghs": 31.20},
    8000: {"label": "8 GB", "price_ghs": 38.20},
    10000: {"label": "10 GB", "price_ghs": 46.00},
    15000: {"label": "15 GB", "price_ghs": 68.00},
    20000: {"label": "20 GB", "price_ghs": 91.00},
    30000: {"label": "30 GB", "price_ghs": 131.00},
    40000: {"label": "40 GB", "price_ghs": 173.00},
    50000: {"label": "50 GB", "price_ghs": 228.00},
    },
    "TELECEL" : {
     10000: {"label": "10 GB", "price_ghs": 39.50},
    15000: {"label": "15 GB", "price_ghs": 57.50},
    20000: {"label": "20 GB", "price_ghs": 76.50},
    25000: {"label": "25 GB", "price_ghs": 93.50},
    30000: {"label": "30 GB", "price_ghs": 116.00},
    35000: {"label": "35 GB", "price_ghs": 136.00},
    40000: {"label": "40 GB", "price_ghs": 148.00},
    45000: {"label": "45 GB", "price_ghs": 169.50},
    50000: {"label": "50 GB", "price_ghs": 189.00},
    },
    "AIRTELTIGO ISHARE" : {
         1000: {"label": "1 GB", "price_ghs": 4.60},
            2000: {"label": "2 GB", "price_ghs": 9.50},
            3000: {"label": "3 GB", "price_ghs": 14.50},
            4000: {"label": "4 GB", "price_ghs": 20.20},
            5000: {"label": "5 GB", "price_ghs": 24.50},
            6000: {"label": "6 GB", "price_ghs": 29.20},
            7000: {"label": "7 GB", "price_ghs": 31.20},
            8000: {"label": "8 GB", "price_ghs": 35.50},
            9000: {"label": "9 GB", "price_ghs": 40.50},
            10000: {"label": "10 GB", "price_ghs": 45.00},
            12000: {"label": "12 GB", "price_ghs": 53.00},
            15000: {"label": "15 GB", "price_ghs": 66.00},
            20000: {"label": "20 GB", "price_ghs": 85.00},
    },
    "AIRTELTIGO BIGTIME" : {
         30000: {"label": "30 GB", "price_ghs": 88.00},
            40000: {"label": "40 GB", "price_ghs": 120.00},
            50000: {"label": "50 GB", "price_ghs": 150.00},
},
}
# Hubnet's documented volume range per transaction (see hubnet_api_doc.pdf,
# section 4: Request Parameters).
MIN_VOLUME_MB = 1
MAX_VOLUME_MB = 100_000


def default_label(volume_mb: int) -> str:
    """Fallback label for a bundle size that has no explicit label (e.g.
    one added without customizing it, or a historical order referencing a
    bundle that's since been removed)."""
    if volume_mb >= 1000 and volume_mb % 1000 == 0:
        return f"{volume_mb // 1000} GB"
    return f"{volume_mb} MB"


def to_pesewas(amount_ghs: float) -> int:
    """Paystack amounts are in the smallest currency unit (pesewas for GHS)."""
    return int(round(amount_ghs * 100))
