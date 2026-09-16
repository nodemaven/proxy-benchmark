"""Generate the committed query lists. Run once, commit the output, forget it.

The benchmark needs a thousand queries that a stranger can reproduce exactly, so
the lists are files in the repository and not something built at runtime. This
script is what built them, kept so the files can be audited and regenerated
rather than trusted.

Two properties are deliberate:

Topics are spread across ten everyday subject areas rather than concentrated on
proxies and scraping. A thousand queries all about anti-detect tooling is a
biased sample of the web, and it hands the target a reason to look closer that
has nothing to do with the framework under test.

The order is shuffled with a fixed seed. Combinatorial generation groups
identical phrasings together, and sending "best X" a hundred times in a row is a
pattern in itself. Shuffling breaks the run of templates while keeping the file
reproducible.

There are three lists because there are three kinds of target. A search engine
answers "photosynthesis exam questions"; a shop answers it with an empty shelf,
and that verdict would be read as the shop refusing us. The product list keeps
the shop's column a measurement of admission rather than of inventory. A map
answers neither, and answers a place query with a feed of businesses. All three
are built the same way from the same seed, so none is the privileged one.

Honest limitation, stated here because it belongs in the report: ten templates
over a hundred subjects is a hundred distinct topics, not a thousand. It
measures a target's reaction to volume and variety of phrasing, not to a
thousand unrelated information needs.

Usage:
    python scripts/tools/build_queries.py            # writes both lists
    python scripts/tools/build_queries.py --check    # verify the files match
"""
import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUERY_DIR = ROOT / "data" / "queries"
SEED = 20260811

SEARCH_CATEGORIES = {
    "consumer_electronics": {
        "terms": ["wireless earbuds", "robot vacuum", "mechanical keyboard",
                  "air fryer", "portable monitor", "electric scooter",
                  "noise cancelling headphones", "smart doorbell",
                  "espresso machine", "gaming laptop"],
        "templates": ["{t}", "best {t}", "{t} reviews", "cheap {t}", "{t} price",
                      "how to choose {t}", "{t} 2026", "{t} deals",
                      "is {t} worth it", "{t} buying guide"],
    },
    "software": {
        "terms": ["python async", "rust borrow checker", "docker compose",
                  "kubernetes ingress", "git rebase", "regex lookahead",
                  "sql window functions", "typescript generics",
                  "grpc streaming", "redis pubsub"],
        "templates": ["{t}", "{t} tutorial", "{t} example", "how does {t} work",
                      "{t} explained", "{t} cheat sheet", "{t} documentation",
                      "{t} error", "learn {t}", "{t} for beginners"],
    },
    "travel": {
        "terms": ["lisbon", "kyoto", "reykjavik", "marrakesh", "buenos aires",
                  "hanoi", "porto", "tbilisi", "seoul", "cape town"],
        "templates": ["flights to {t}", "hotels in {t}", "what to do in {t}",
                      "best time to visit {t}", "{t} travel guide",
                      "is {t} safe", "{t} public transport", "{t} street food",
                      "cheap hostels {t}", "{t} weather in march"],
    },
    "health": {
        "terms": ["vitamin d", "intermittent fasting", "resting heart rate",
                  "sleep apnea", "magnesium supplement", "plantar fasciitis",
                  "blood pressure", "iron deficiency", "tension headache",
                  "lactose intolerance"],
        "templates": ["{t}", "{t} symptoms", "{t} treatment", "what causes {t}",
                      "{t} explained", "{t} home remedies", "{t} in adults",
                      "when to see a doctor {t}", "{t} diet", "{t} nhs"],
    },
    "finance": {
        "terms": ["index funds", "mortgage rates", "credit score",
                  "roth ira", "corporate bonds", "capital gains tax",
                  "emergency fund", "dividend stocks", "currency hedging",
                  "student loan refinancing"],
        "templates": ["{t}", "{t} explained", "how do {t} work", "best {t}",
                      "{t} calculator", "{t} for beginners", "{t} vs savings",
                      "{t} 2026", "risks of {t}", "{t} comparison"],
    },
    "cooking": {
        "terms": ["sourdough starter", "risotto", "kimchi", "beef wellington",
                  "cold brew coffee", "gluten free bread", "ramen broth",
                  "pizza dough", "creme brulee", "pulled pork"],
        "templates": ["{t} recipe", "how to make {t}", "{t} for beginners",
                      "easy {t}", "{t} mistakes", "authentic {t}",
                      "{t} without oven", "quick {t}", "{t} step by step",
                      "best {t} recipe"],
    },
    "automotive": {
        "terms": ["electric car charging", "winter tyres", "hybrid battery life",
                  "brake pad replacement", "car insurance excess",
                  "timing belt", "diesel particulate filter", "car lease deals",
                  "engine oil grades", "dashcam"],
        "templates": ["{t}", "{t} cost", "{t} explained", "best {t}",
                      "how often {t}", "{t} problems", "{t} reviews",
                      "{t} vs alternatives", "{t} guide", "cheap {t}"],
    },
    "education": {
        "terms": ["linear algebra", "organic chemistry", "macroeconomics",
                  "spanish grammar", "music theory", "statistics regression",
                  "world war one causes", "photosynthesis", "calculus limits",
                  "creative writing"],
        "templates": ["{t}", "{t} explained", "{t} course", "learn {t} online",
                      "{t} practice problems", "{t} for beginners",
                      "{t} textbook", "{t} lecture notes", "{t} exam questions",
                      "{t} summary"],
    },
    "home": {
        "terms": ["heat pump", "loft insulation", "damp proofing",
                  "solar panels", "underfloor heating", "double glazing",
                  "kitchen extractor", "garden decking", "smart thermostat",
                  "septic tank"],
        "templates": ["{t}", "{t} cost", "{t} installation", "is {t} worth it",
                      "{t} grants", "{t} reviews", "{t} problems",
                      "how does {t} work", "{t} vs alternatives",
                      "best {t} 2026"],
    },
    "sport": {
        "terms": ["marathon training", "bouldering grades", "swimming technique",
                  "cycling power meter", "tennis serve", "kettlebell workout",
                  "football offside rule", "yoga for back pain",
                  "running shoes drop", "rowing machine"],
        "templates": ["{t}", "{t} explained", "{t} for beginners",
                      "{t} plan", "best {t}", "{t} mistakes", "{t} tips",
                      "how to improve {t}", "{t} equipment", "{t} guide"],
    },
}


# What a shopper types, which is a different shape from what a searcher types:
# an object plus a constraint, not a question.
#
# Every template has to read as a sentence with every term in its own category,
# and the categories below are narrowed until that holds. "{t} braided" is a
# search for a cable and nonsense next to a keyboard; the shop would answer the
# nonsense with an empty shelf, and an empty shelf is indistinguishable from a
# soft refusal in the row. Keeping the queries answerable is what makes an
# `s-no-results` page evidence about the query list rather than about the target.
PRODUCT_CATEGORIES = {
    "kitchen": {
        "terms": ["air fryer", "cast iron skillet", "espresso machine",
                  "stand mixer", "chef knife", "food processor",
                  "electric kettle", "dutch oven", "rice cooker",
                  "cutting board"],
        "templates": ["{t}", "best {t}", "{t} small", "{t} stainless steel",
                      "{t} under 100", "{t} set", "{t} large",
                      "compact {t}", "{t} for small kitchen",
                      "{t} dishwasher safe"],
    },
    "audio": {
        "terms": ["wireless earbuds", "bluetooth speaker", "over ear headphones",
                  "gaming headset", "bone conduction headphones",
                  "portable radio", "mp3 player", "sleep headphones",
                  "kids headphones", "waterproof speaker"],
        "templates": ["{t}", "best {t}", "{t} cheap", "{t} bluetooth",
                      "{t} for travel", "{t} with long battery", "{t} under 50",
                      "{t} reviews", "{t} black", "{t} with case"],
    },
    "desk_tech": {
        "terms": ["usb c hub", "portable monitor", "mechanical keyboard",
                  "wireless mouse", "external ssd", "laptop stand",
                  "webcam 1080p", "docking station", "desk mat",
                  "monitor arm"],
        "templates": ["{t}", "best {t}", "{t} for macbook", "{t} for office",
                      "{t} compact", "{t} cheap", "{t} black",
                      "{t} under 100", "{t} reviews", "{t} for home office"],
    },
    "furniture": {
        "terms": ["standing desk", "office chair", "bookshelf", "bed frame",
                  "shoe rack", "coffee table", "filing cabinet", "wardrobe",
                  "sideboard", "storage ottoman"],
        "templates": ["{t}", "best {t}", "{t} white", "{t} oak", "{t} small",
                      "{t} grey", "{t} adjustable", "{t} for bedroom",
                      "{t} under 200", "{t} no assembly"],
    },
    "tools": {
        "terms": ["cordless drill", "socket set", "circular saw",
                  "multimeter", "tool box", "spirit level", "angle grinder",
                  "torque wrench", "heat gun", "stud finder"],
        "templates": ["{t}", "best {t}", "{t} with case", "{t} professional",
                      "{t} for home use", "{t} cheap", "{t} kit",
                      "{t} reviews", "{t} heavy duty", "{t} small"],
    },
    "outdoor": {
        "terms": ["camping tent", "sleeping bag", "hiking backpack",
                  "camping stove", "trekking poles", "head torch",
                  "water filter bottle", "picnic blanket", "camping chair",
                  "cool box"],
        "templates": ["{t}", "best {t}", "{t} compact", "{t} lightweight",
                      "{t} waterproof", "{t} for winter", "{t} reviews",
                      "{t} cheap", "{t} for backpacking", "{t} large"],
    },
    "fitness": {
        "terms": ["yoga mat", "adjustable dumbbells", "resistance bands",
                  "kettlebell", "foam roller", "pull up bar", "jump rope",
                  "exercise bike", "weight bench", "ab roller"],
        "templates": ["{t}", "best {t}", "{t} for home gym", "{t} cheap",
                      "{t} set", "{t} compact", "{t} reviews",
                      "{t} for beginners", "{t} under 100",
                      "{t} for small spaces"],
    },
    "pet": {
        "terms": ["dog bed", "cat tree", "pet carrier", "dog harness",
                  "automatic feeder", "litter box", "grooming brush",
                  "aquarium filter", "dog crate", "cat scratching post"],
        "templates": ["{t}", "best {t}", "{t} large", "{t} small",
                      "{t} washable", "{t} reviews", "{t} easy clean",
                      "{t} for home", "{t} cheap", "{t} heavy duty"],
    },
    "automotive": {
        "terms": ["dash cam", "car phone mount", "jump starter",
                  "tyre inflator", "car vacuum", "obd2 scanner",
                  "roof box", "seat covers", "windscreen wipers",
                  "car battery charger"],
        "templates": ["{t}", "best {t}", "{t} reviews", "{t} compact",
                      "{t} black", "{t} universal", "{t} for winter",
                      "{t} cheap", "{t} for suv", "{t} heavy duty"],
    },
    "personal_care": {
        "terms": ["electric toothbrush", "hair clippers", "electric shaver",
                  "hair dryer", "hair straightener", "epilator",
                  "beard trimmer", "facial cleansing brush", "bathroom scale",
                  "massage gun"],
        "templates": ["{t}", "best {t}", "{t} rechargeable", "{t} reviews",
                      "{t} travel size", "{t} with case", "{t} cheap",
                      "{t} professional", "{t} compact", "{t} under 50"],
    },
}

# What somebody looking for a business types: a trade and a place, not a question
# and not an object with a constraint. Maps answers a product query with a page
# that has no places on it, and that page cannot be told from a refused one after
# the fact - the `s-no-results` problem with no `s-no-results` to read.
#
# The category is the region and the templates are its trades, which is the same
# shape the two lists above use and it is load-bearing here for a second reason:
# a trade has to exist in every city of its own category, and regions are how
# that is kept true. `sauna in {t}` over the Nordics reads; over Latin America it
# would be a query about nothing.
#
# **Every query names its city.** That is what makes the list usable on an
# arbitrary exit: Maps answers "dentist in Austin Texas" with Austin dentists
# whether it was asked from Texas or from Sao Paulo, so a pass rate measured
# across a rotating pool is a measurement of admission and not of where the exit
# happened to land. A bare "dentist" would make every row depend on the exit's
# geoip and the column would be unreadable.
PLACE_CATEGORIES = {
    "north_america": {
        "terms": ["Austin Texas", "Portland Oregon", "Toronto", "Vancouver",
                  "Chicago", "Denver", "Montreal", "San Diego", "Nashville",
                  "Calgary"],
        "templates": ["dentist in {t}", "plumber in {t}", "coffee shop in {t}",
                      "gym in {t}", "car repair in {t}", "pharmacy in {t}",
                      "hardware store in {t}", "immigration lawyer in {t}",
                      "veterinarian in {t}", "locksmith in {t}"],
    },
    "british_isles": {
        "terms": ["Manchester", "Edinburgh", "Dublin", "Bristol", "Leeds",
                  "Glasgow", "Cardiff", "Belfast", "Brighton", "Cork"],
        "templates": ["bakery in {t}", "bookshop in {t}", "barber in {t}",
                      "dry cleaner in {t}", "optician in {t}",
                      "bike shop in {t}", "physiotherapist in {t}",
                      "tattoo studio in {t}", "garden centre in {t}",
                      "letting agent in {t}"],
    },
    "western_europe": {
        "terms": ["Berlin", "Lisbon", "Rotterdam", "Lyon", "Antwerp", "Munich",
                  "Porto", "Bordeaux", "Utrecht", "Ghent"],
        "templates": ["bakery in {t}", "bicycle repair in {t}", "dentist in {t}",
                      "notary in {t}", "physiotherapist in {t}",
                      "language school in {t}", "coworking space in {t}",
                      "florist in {t}", "shoe repair in {t}", "wine shop in {t}"],
    },
    "nordics": {
        "terms": ["Stockholm", "Oslo", "Helsinki", "Copenhagen", "Gothenburg",
                  "Bergen", "Aarhus", "Tampere", "Malmo", "Trondheim"],
        "templates": ["sauna in {t}", "dentist in {t}", "coffee roastery in {t}",
                      "bike shop in {t}", "optician in {t}",
                      "dry cleaner in {t}", "veterinarian in {t}",
                      "hair salon in {t}", "tailor in {t}", "pharmacy in {t}"],
    },
    "central_europe": {
        "terms": ["Warsaw", "Prague", "Budapest", "Krakow", "Vienna",
                  "Bratislava", "Wroclaw", "Brno", "Ljubljana", "Gdansk"],
        "templates": ["dentist in {t}", "currency exchange in {t}",
                      "hostel in {t}", "physiotherapist in {t}",
                      "car rental in {t}", "laundromat in {t}", "tailor in {t}",
                      "optician in {t}", "veterinarian in {t}", "bakery in {t}"],
    },
    "southern_europe": {
        "terms": ["Barcelona", "Milan", "Athens", "Valencia", "Bologna",
                  "Seville", "Thessaloniki", "Turin", "Malaga", "Palermo"],
        "templates": ["notary in {t}", "locksmith in {t}", "tailor in {t}",
                      "physiotherapist in {t}", "car rental in {t}",
                      "laundromat in {t}", "hair salon in {t}",
                      "optician in {t}", "bike rental in {t}",
                      "pharmacy in {t}"],
    },
    "southeast_asia": {
        "terms": ["Singapore", "Kuala Lumpur", "Bangkok", "Jakarta", "Manila",
                  "Ho Chi Minh City", "Penang", "Cebu", "Hanoi", "Surabaya"],
        "templates": ["coffee shop in {t}", "phone repair in {t}",
                      "laundry service in {t}", "money changer in {t}",
                      "motorcycle rental in {t}", "dental clinic in {t}",
                      "coworking space in {t}", "tailor in {t}",
                      "printing shop in {t}", "car workshop in {t}"],
    },
    "east_asia": {
        "terms": ["Tokyo", "Seoul", "Osaka", "Taipei", "Hong Kong", "Fukuoka",
                  "Busan", "Kyoto", "Kaohsiung", "Nagoya"],
        "templates": ["coffee shop in {t}", "phone repair in {t}",
                      "dental clinic in {t}", "hair salon in {t}",
                      "stationery shop in {t}", "camera shop in {t}",
                      "coworking space in {t}", "laundry service in {t}",
                      "bicycle shop in {t}", "optician in {t}"],
    },
    "oceania": {
        "terms": ["Sydney", "Melbourne", "Auckland", "Brisbane", "Perth",
                  "Wellington", "Adelaide", "Christchurch", "Hobart",
                  "Canberra"],
        "templates": ["dentist in {t}", "plumber in {t}", "electrician in {t}",
                      "cafe in {t}", "gym in {t}", "veterinarian in {t}",
                      "car repair in {t}", "physiotherapist in {t}",
                      "hardware store in {t}", "hair salon in {t}"],
    },
    "latin_america": {
        "terms": ["Mexico City", "Buenos Aires", "Santiago", "Bogota", "Lima",
                  "Sao Paulo", "Montevideo", "Guadalajara", "Medellin", "Quito"],
        "templates": ["dentist in {t}", "notary in {t}",
                      "currency exchange in {t}", "laundry service in {t}",
                      "phone repair in {t}", "hair salon in {t}",
                      "veterinarian in {t}", "car workshop in {t}",
                      "bakery in {t}", "pharmacy in {t}"],
    },
}

# Name on disk -> the categories it is built from. Adding a third kind of target
# is an entry here, not a second copy of this script.
LISTS = {
    "serp_1000": SEARCH_CATEGORIES,
    "amazon_1000": PRODUCT_CATEGORIES,
    "places_1000": PLACE_CATEGORIES,
}


def build(categories: dict) -> list:
    queries = []
    for category in sorted(categories):
        block = categories[category]
        for term in block["terms"]:
            for template in block["templates"]:
                queries.append(template.format(t=term))

    if len(queries) != len(set(queries)):
        duplicates = len(queries) - len(set(queries))
        raise SystemExit(f"{duplicates} duplicate queries: a target seeing the "
                         f"same string twice makes the run unreadable")

    random.Random(SEED).shuffle(queries)
    return queries


def render(name: str) -> str:
    queries = build(LISTS[name])
    header = [
        "# Generated once by scripts/tools/build_queries.py and committed.",
        f"# list={name}  seed={SEED}  categories={len(LISTS[name])}  "
        f"queries={len(queries)}",
        "# Do not edit by hand: regenerate so the seed and the file stay in step.",
    ]
    return "\n".join(header + queries) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", default="all", choices=["all", *sorted(LISTS)],
                        help="which committed list to write or verify")
    parser.add_argument("--check", action="store_true",
                        help="fail if a committed file differs from what "
                             "this script would generate now")
    args = parser.parse_args()

    names = sorted(LISTS) if args.list == "all" else [args.list]
    for name in names:
        path = QUERY_DIR / f"{name}.txt"
        content = render(name)
        count = len(content.splitlines()) - 3

        if args.check:
            if not path.exists():
                raise SystemExit(f"{path} is missing")
            if path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"{path} does not match the generator: "
                                 f"regenerate it")
            print(f"{path.relative_to(ROOT)} matches the generator, "
                  f"{count} queries")
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {count} queries to {path.relative_to(ROOT)}")
        print(f"first five: {content.splitlines()[3:8]}")


if __name__ == "__main__":
    sys.exit(main())
