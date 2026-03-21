# app.py
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import urllib.parse
import requests
from bs4 import BeautifulSoup
import re
import time
import json
from datetime import datetime
from email.utils import parsedate_to_datetime
from collections import Counter
import concurrent.futures
import math

app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

scan_history = []
stats = {"total": 0, "fake": 0, "real": 0}

# ── Thumbnail cache — prevents re-scraping the same URL every request ────────
THUMBNAIL_CACHE = {}          # url → {"image": str, "ts": float}
CACHE_EXPIRY_SECONDS = 3600   # 1 hour

NEWS_API_KEY      = "74451a00a3da4031907869f3295f3683"
GOOGLE_FC_API_KEY = "AIzaSyD4rl4NbYwWgaJ35z6P3KI4BUYlFxmZMBs"
GROQ_API_KEY      = "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"
COHERE_API_KEY    = "dguuEWbNOKc8jbW6QFOB7QTC2igY49DWa2qWWXJL"
WIKIPEDIA_API     = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIPEDIA_HI_API  = "https://hi.wikipedia.org/api/rest_v1/page/summary/"

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.unixfox.eu",
    "https://nitter.net",
    "https://nitter.cz",
    "https://nitter.it",
    "https://nitter.nl",
    "https://nitter.mint.lgbt",
    "https://nitter.fdn.fr",
]

# ── Hindi news domains for prioritized Indian-language searches ──────────────
HINDI_NEWS_DOMAINS = (
    "aajtak.in,zeenews.india.com,abplive.com,ndtv.com,ndtv.in,"
    "bhaskar.com,jagran.com,amarujala.com,news18.com,"
    "hindustantimes.com,indiatoday.in,thehindu.com,"
    "navbharattimes.indiatimes.com,livehindustan.com,"
    "jansatta.com,patrika.com,punjabkesari.in"
)

# ── Hindi topic → English keyword translation ─────────────────────────────────
HINDI_TO_ENGLISH_TOPICS = {
    "विश्व कप":      "World Cup cricket",
    "क्रिकेट":       "cricket India",
    "t20":           "T20 cricket India",
    "आईपीएल":        "IPL cricket",
    "रोहित शर्मा":  "Rohit Sharma cricket",
    "विराट कोहली":  "Virat Kohli cricket",
    "भारत":          "India",
    "टीम इंडिया":   "Team India cricket",
    "चंद्रयान":      "Chandrayaan ISRO moon",
    "इसरो":          "ISRO India space",
    "गगनयान":        "Gaganyaan ISRO",
    "मोदी":          "Modi India",
    "प्रधानमंत्री":  "Prime Minister India",
    "संसद":          "Parliament India",
    "चुनाव":         "Election India",
    "भाजपा":         "BJP India",
    "कांग्रेस":      "Congress India",
    "अर्थव्यवस्था": "India economy",
    "बजट":           "India budget",
    "शेयर बाजार":   "India stock market Sensex",
    "कोरोना":        "coronavirus COVID India",
    "वैक्सीन":       "vaccine India",
    "5g":            "5G India",
    "माइक्रोचिप":   "microchip vaccine conspiracy",
    "फेक न्यूज":    "fake news India misinformation",
}


def _hindi_to_english_keywords(claim_text):
    text_lower = claim_text.lower()
    matched = []
    for hi_phrase, en_keywords in HINDI_TO_ENGLISH_TOPICS.items():
        if hi_phrase.lower() in text_lower:
            matched.extend(en_keywords.split()[:3])
    en_words = re.findall(r'\b[a-zA-Z0-9]{2,}\b', claim_text)
    en_words = [w for w in en_words if len(w) >= 2 and w.upper() not in
                {"KA", "KI", "KE", "ME", "SE", "HE", "YE", "WO", "IS", "US"}]
    combined = matched + en_words
    seen = set()
    result = []
    for w in combined:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            result.append(w)
    return result[:6]

VERIFIED_EVENTS_KB = [
    ("india won icc t20 world cup 2024", "REAL", "India won the ICC T20 World Cup 2024, defeating South Africa in the final in Barbados."),
    ("india beat south africa t20 world cup", "REAL", "India won ICC T20 World Cup 2024."),
    ("india t20 world cup 2024", "REAL", "India won ICC T20 World Cup 2024."),
    ("rohit sharma t20 world cup", "REAL", "India won ICC T20 World Cup 2024 under captain Rohit Sharma."),
    ("virat kohli world cup final", "REAL", "Virat Kohli scored 76 runs in the T20 World Cup 2024 final."),
    ("chandrayaan-3 moon landing", "REAL", "ISRO's Chandrayaan-3 successfully landed near the Moon's south pole on August 23, 2023."),
    ("isro chandrayaan moon", "REAL", "Chandrayaan-3 successfully landed on the Moon in 2023."),
    ("gaganyaan isro", "REAL", "ISRO's Gaganyaan is India's human spaceflight mission."),
    ("chatgpt 300 million users", "REAL", "OpenAI's ChatGPT surpassed 300 million weekly active users."),
    ("sam altman fired openai reinstated", "REAL", "Sam Altman was fired and reinstated as OpenAI CEO in November 2023."),
    ("elon musk twitter acquisition", "REAL", "Elon Musk completed his $44B acquisition of Twitter and rebranded it to X in 2022."),
    ("alphafold protein folding", "REAL", "Google DeepMind's AlphaFold solved the protein-folding problem."),
    ("russia invaded ukraine 2022", "REAL", "Russia launched a full-scale invasion of Ukraine in February 2022."),
    ("russia ukraine war", "REAL", "Russia invaded Ukraine in February 2022."),
    ("hamas attack israel october 7 2023", "REAL", "Hamas launched a surprise attack on Israel on October 7, 2023, killing over 1,200 people."),
    ("trump won 2024 presidential election", "REAL", "Donald Trump won the 2024 US presidential election, defeating Kamala Harris."),
    ("trump wins 2024 election", "REAL", "Donald Trump won the 2024 US presidential election."),
    ("apple record revenue iphone", "REAL", "Apple reported record quarterly revenue following the iPhone launch."),
    ("deepmind alphafold protein", "REAL", "Google DeepMind AlphaFold solved protein folding, a major scientific breakthrough."),
    ("भारत t20 विश्व कप 2024", "REAL", "भारत ने ICC T20 विश्व कप 2024 जीता।"),
    ("चंद्रयान-3 चंद्रमा", "REAL", "इसरो ने चंद्रयान-3 को चंद्रमा पर सफलतापूर्वक उतारा।"),
]

KNOWN_MISINFORMATION_KB = [
    ("5g towers spread covid", "FAKE", "COVID-19 / 5G misinformation — no scientific evidence links 5G to COVID-19."),
    ("5g causes coronavirus", "FAKE", "COVID-19 / 5G misinformation — thoroughly debunked by WHO and scientific consensus."),
    ("vaccine contains microchip", "FAKE", "Vaccine microchip conspiracy — no microchips are present in any COVID-19 vaccine."),
    ("vaccines contain tracking chips", "FAKE", "Vaccine microchip conspiracy — thoroughly debunked by medical authorities."),
    ("bill gates microchip vaccine", "FAKE", "Vaccine microchip conspiracy — debunked by fact-checkers globally."),
    ("bleach cures covid", "FAKE", "Dangerous health misinformation — drinking bleach is lethal and has no antiviral effect."),
    ("drinking disinfectant coronavirus", "FAKE", "Dangerous health misinformation — thoroughly debunked by WHO and CDC."),
    ("climate change is a hoax", "FAKE", "Scientific misinformation — climate change is confirmed by overwhelming scientific consensus."),
    ("global warming fake", "FAKE", "Scientific misinformation — global warming is backed by thousands of peer-reviewed studies."),
    ("moon landing was faked", "FAKE", "Apollo moon landing conspiracy — thoroughly debunked; the 1969 landing is documented fact."),
    ("nasa faked moon landing", "FAKE", "Apollo moon landing conspiracy — thoroughly debunked."),
    ("flat earth proven", "FAKE", "Flat earth misinformation — Earth is an oblate spheroid confirmed by centuries of science."),
    ("earth is flat", "FAKE", "Flat earth misinformation — thoroughly debunked."),
    ("trump secretly arrested", "FAKE", "Political misinformation — no credible source reported Trump being secretly arrested."),
    ("nasa 15 days darkness", "FAKE", "NASA darkness hoax — thoroughly debunked; no such event was announced by NASA."),
    ("15 days of darkness nasa confirmed", "FAKE", "NASA darkness hoax — repeatedly debunked each year."),
    ("zuckerberg reptilian", "FAKE", "Fringe conspiracy — no evidence Mark Zuckerberg or any public figure is a 'reptilian'."),
    ("soros funding conspiracy protesters", "FAKE", "Conspiracy rhetoric — claims of secret Soros funding destabilizing America are unsubstantiated."),
    ("deep state hiding truth", "FAKE", "Deep state conspiracy language — not supported by credible evidence."),
    ("new world order conspiracy", "FAKE", "New World Order conspiracy — debunked fringe theory."),
    ("chemtrails are poison", "FAKE", "Chemtrail conspiracy — aircraft condensation trails are water vapor, not chemical sprays."),
    ("illuminati controls governments", "FAKE", "Illuminati conspiracy — the historical Illuminati dissolved in 1785; modern claims are baseless."),
    ("5g टावर कोरोना", "FAKE", "5G और COVID-19 को जोड़ने वाला दावा झूठा है।"),
    ("वैक्सीन माइक्रोचिप बिल गेट्स", "FAKE", "वैक्सीन में माइक्रोचिप का दावा पूरी तरह झूठ है।"),
    ("apollo moon landing stanley kubrick", "FAKE", "Moon landing conspiracy — thoroughly debunked hoax claim."),
    ("george soros black lives matter funding destabilize", "FAKE", "Unsubstantiated conspiracy — no credible evidence supports this claim."),
    ("illuminati controls world governments financial", "FAKE", "Illuminati conspiracy theory — debunked fringe claim with no factual basis."),
]

SUSPICIOUS_PATTERNS = [
    (r'\b(miracle|secret|exposed|shocking|they don\'t want|hidden truth)\b', "Sensational language"),
    (r'\b(conspiracy|cover.?up|deep state|hidden truth|mainstream media hiding)\b', "Conspiracy language"),
    (r'(!{2,}|\?{2,})', "Excessive punctuation"),
    (r'\b(aliens|illuminati|reptilian|microchip in vaccine|chemtrail)\b', "Fringe claims"),
    (r'\b(100%|guaranteed|banned|suppressed by government)\b', "Absolute claims"),
    (r'\b(wake up|sheeple|they are hiding|big pharma|new world order)\b', "Conspiracy rhetoric"),
    (r'(षड्यंत्र|छुपाया|सनसनी|चौंकाने वाला|खुलासा|झूठ|धोखा|सरकार छुपा)', "Hindi suspicious language"),
    (r'\b(secretly|government hiding|officials suppressing|media blackout)\b', "Cover-up language"),
    (r'\b(hoax|fabricated|faked by|crisis actor)\b', "Hoax language"),
]

CREDIBLE_PATTERNS = [
    (r'\b(according to|study (shows|finds|suggests)|researchers? (found|said|confirmed))\b', "Cites sources"),
    (r'\b(official|government|parliament|minister|published|journal)\b', "Official references"),
    (r'\b(percent|data|statistics|survey|report|confirmed)\b', "Data-backed language"),
    (r'\b(nasa|isro|who|un|reuters|bbc|associated press|ap news)\b', "Credible organisations"),
    (r'\b(peer.reviewed|clinical trial|meta.analysis|published in)\b', "Academic language"),
    (r'(सरकार|संसद|आधिकारिक|रिपोर्ट|शोध|अध्ययन|विशेषज्ञ|इसरो)', "Hindi official reference"),
    (r'\b(won|defeated|beat|launched|landed|signed|passed|approved)\b', "Factual action verbs"),
    (r'\b(quarter|billion|million|revenue|election|vote|won)\b', "Quantified facts"),
]

STRONG_REAL_SIGNALS = [
    r'\b(supreme court (ruled|ordered|directed|upheld|dismissed))\b',
    r'\b(parliament (passed|approved|rejected|introduced))\b',
    r'\b(rbi (announced|raised|cut|held|governor|monetary policy))\b',
    r'\b(isro (launched|successfully|mission|satellite|chandrayaan|gaganyaan|aditya))\b',
    r'\b(budget (presented|allocated|announced|2024|2025|2026))\b',
    r'\b(election commission|lok sabha elections?|assembly elections?)\b',
    r'\b(who (confirmed|declared|announced|warned|recommended))\b',
    r'\b(published in (nature|lancet|science|nejm|bmj))\b',
    r'\b(india won|india beat|india vs).{0,40}(world cup|trophy|final|championship)\b',
    r'\b(chandrayaan|gaganyaan|aditya.l1|pslv|gslv)\b',
    r'\b(pm modi|prime minister modi|president droupadi|g20|brics)\b',
    r'\b(trump won|trump wins|trump elected|trump victory).{0,30}(2024|election|president)\b',
    r'\b(russia invaded|russia launched.{0,20}invasion|russian forces entered) ukraine\b',
    r'\b(hamas.{0,20}attack|hamas.{0,20}killed|october 7.{0,20}israel)\b',
    r'\b(sam altman.{0,30}(fired|reinstated|ceo)|openai.{0,20}ceo)\b',
    r'\b(elon musk.{0,20}(bought|acquired|twitter|x\.com))\b',
    r'\b(chatgpt.{0,30}(million|users|weekly|active))\b',
    r'\b(alphafold.{0,30}(protein|breakthrough|deepmind))\b',
    r'\b(apple.{0,30}(revenue|billion|record|iphone 1[5-9]))\b',
    r'(इसरो|चंद्रयान|गगनयान|संसद|सुप्रीम कोर्ट|प्रधानमंत्री मोदी)',
    r'\b(nifty|sensex|bse|nse).{0,20}(rose|fell|gained|lost|closed)\b',
]

STRONG_FAKE_SIGNALS = [
    r'\b(secretly arrested|secret arrest).{0,30}(fbi|government|deep state)\b',
    r'\b(5g).{0,20}(covid|coronavirus|spread|cause)\b',
    r'\b(vaccine|covid).{0,20}(microchip|tracking chip|bill gates chip)\b',
    r'\b(bleach|disinfectant).{0,20}(cure|kill|treat).{0,20}(covid|corona|virus)\b',
    r'\b(moon landing).{0,30}(fake|faked|hoax|fabricat|stanley kubrick)\b',
    r'\b(climate change|global warming).{0,20}(hoax|fake|fabricat|not real)\b',
    r'\b(15 days.{0,10}darkness|days of darkness).{0,30}(nasa|confirmed|earth)\b',
    r'\b(zuckerberg|politician|celebrity).{0,20}(reptilian|alien|lizard)\b',
    r'\b(illuminati controls|illuminati runs).{0,20}(world|government|banks)\b',
    r'\b(soros.{0,30}(funding|paying|financing).{0,30}(protest|destabilize|destroy))\b',
    r'\b(earth is flat|flat earth proven|proven flat)\b',
    r'\b(new world order).{0,20}(control|takeover|establishment|2030)\b',
    r'\b(chemtrail).{0,20}(poison|spray|chemical|toxic)\b',
    r'\b(5g टावर).{0,20}(कोरोना|covid)\b',
    r'\b(apollo).{0,20}(faked|fake|kubrick|hoax|studio)\b',
]

DEBUNK_SIGNALS = [
    "fact check", "fact-check", "factcheck", "debunked", "false claim",
    "misleading", "misinformation", "no evidence", "fake news", "hoax",
    "not true", "incorrect", "fabricated", "viral claim", "rumor", "rumour",
    "false", "wrong", "myth", "busted", "snopes", "politifact", "altnews",
    "boomlive", "alt news", "boom live",
    "फेक न्यूज", "फर्जी", "झूठी खबर", "भ्रामक", "फैक्ट चेक",
    "गलत दावा", "अफवाह", "भ्रम", "सच नहीं",
]

SOURCE_FAVICONS = {
    "the hindu": "https://www.thehindu.com/favicon.ico",
    "ndtv": "https://www.ndtv.com/favicon.ico",
    "ndtv india": "https://www.ndtv.com/favicon.ico",
    "india today": "https://www.indiatoday.in/favicon.ico",
    "aaj tak": "https://www.aajtak.in/favicon.ico",
    "aajtak": "https://www.aajtak.in/favicon.ico",
    "zee news": "https://zeenews.india.com/favicon.ico",
    "republic world": "https://www.republicworld.com/favicon.ico",
    "republic": "https://www.republicworld.com/favicon.ico",
    "dainik bhaskar": "https://www.bhaskar.com/favicon.ico",
    "navbharat times": "https://navbharattimes.indiatimes.com/favicon.ico",
    "bbc news": "https://www.bbc.com/favicon.ico",
    "bbc": "https://www.bbc.com/favicon.ico",
    "bbc hindi": "https://www.bbc.com/favicon.ico",
    "reuters": "https://www.reuters.com/favicon.ico",
    "the guardian": "https://www.theguardian.com/favicon.ico",
    "cnn": "https://www.cnn.com/favicon.ico",
    "al jazeera": "https://www.aljazeera.com/favicon.ico",
    "the wire": "https://thewire.in/favicon.ico",
    "scroll.in": "https://scroll.in/favicon.ico",
    "the print": "https://theprint.in/favicon.ico",
    "snopes": "https://www.snopes.com/favicon.ico",
    "politifact": "https://www.politifact.com/favicon.ico",
    "altnews": "https://www.altnews.in/favicon.ico",
    "alt news": "https://www.altnews.in/favicon.ico",
    "boomlive": "https://www.boomlive.in/favicon.ico",
    "washington post": "https://www.washingtonpost.com/favicon.ico",
    "new york times": "https://www.nytimes.com/favicon.ico",
    "associated press": "https://apnews.com/favicon.ico",
    "ap news": "https://apnews.com/favicon.ico",
    "economic times": "https://economictimes.indiatimes.com/favicon.ico",
    "business standard": "https://www.business-standard.com/favicon.ico",
    "bloomberg": "https://www.bloomberg.com/favicon.ico",
    "forbes": "https://www.forbes.com/favicon.ico",
    "espncricinfo": "https://www.espncricinfo.com/favicon.ico",
    "yahoo news": "https://news.yahoo.com/favicon.ico",
    "hindustan times": "https://www.hindustantimes.com/favicon.ico",
    "times of india": "https://static.toiimg.com/favicon.ico",
    "abc news": "https://abcnews.go.com/favicon.ico",
    "nbc news": "https://www.nbcnews.com/favicon.ico",
    "fox news": "https://www.foxnews.com/favicon.ico",
    "indian express": "https://indianexpress.com/favicon.ico",
    "mint": "https://www.livemint.com/favicon.ico",
    "livemint": "https://www.livemint.com/favicon.ico",
    "abp live": "https://www.abplive.com/favicon.ico",
    "abp news": "https://www.abplive.com/favicon.ico",
    "jagran": "https://www.jagran.com/favicon.ico",
    "dainik jagran": "https://www.jagran.com/favicon.ico",
    "amar ujala": "https://www.amarujala.com/favicon.ico",
    "news18": "https://www.news18.com/favicon.ico",
    "news18 india": "https://www.news18.com/favicon.ico",
}

WIKI_TOPIC_MAP = {
    "chatgpt 300 million users":    "ChatGPT",
    "chatgpt weekly active users":  "ChatGPT",
    "chatgpt":                       "ChatGPT",
    "openai chatgpt":                "ChatGPT",
    "openai":                        "OpenAI",
    "sam altman fired openai":       "Sam Altman",
    "sam altman reinstated":         "Sam Altman",
    "sam altman":                    "Sam Altman",
    "elon musk twitter acquisition": "Acquisition of Twitter by Elon Musk",
    "elon musk bought twitter":      "Acquisition of Twitter by Elon Musk",
    "elon musk acquires twitter":    "Acquisition of Twitter by Elon Musk",
    "elon musk twitter":             "Acquisition of Twitter by Elon Musk",
    "twitter acquisition":           "Acquisition of Twitter by Elon Musk",
    "twitter renamed x":             "Acquisition of Twitter by Elon Musk",
    "elon musk":                     "Elon Musk",
    "tesla":                         "Tesla, Inc.",
    "zuckerberg reptilian":          "Mark Zuckerberg",
    "zuckerberg alien":              "Mark Zuckerberg",
    "mark zuckerberg":               "Mark Zuckerberg",
    "zuckerberg":                    "Mark Zuckerberg",
    "meta facebook":                 "Meta Platforms",
    "facebook":                      "Facebook",
    "trump secretly arrested fbi":   "Donald Trump",
    "trump arrested fbi":            "Donald Trump",
    "trump fbi":                     "Donald Trump",
    "trump deep state":              "Donald Trump",
    "trump won 2024":                "2024 United States presidential election",
    "trump wins 2024":               "2024 United States presidential election",
    "trump 2024 election":           "2024 United States presidential election",
    "trump elected president 2024":  "2024 United States presidential election",
    "trump president 2024":          "2024 United States presidential election",
    "trump wins election":           "2024 United States presidential election",
    "us election 2024":              "2024 United States presidential election",
    "us presidential election 2024": "2024 United States presidential election",
    "presidential election 2024":    "2024 United States presidential election",
    "american election 2024":        "2024 United States presidential election",
    "2024 election":                 "2024 United States presidential election",
    "donald trump":                  "Donald Trump",
    "trump":                         "Donald Trump",
    "kamala harris":                 "Kamala Harris",
    "joe biden":                     "Joe Biden",
    "biden":                         "Joe Biden",
    "russia invaded ukraine":        "2022 Russian invasion of Ukraine",
    "russia launched invasion":      "2022 Russian invasion of Ukraine",
    "russia ukraine war":            "2022 Russian invasion of Ukraine",
    "russia ukraine 2022":           "2022 Russian invasion of Ukraine",
    "ukraine war":                   "2022 Russian invasion of Ukraine",
    "russia ukraine":                "2022 Russian invasion of Ukraine",
    "ukraine russia":                "2022 Russian invasion of Ukraine",
    "putin ukraine":                 "2022 Russian invasion of Ukraine",
    "zelensky":                      "Volodymyr Zelenskyy",
    "putin":                         "Vladimir Putin",
    "october 7 hamas attack":        "2023 Hamas-led attack on Israel",
    "hamas october 7":               "2023 Hamas-led attack on Israel",
    "october 7 israel":              "2023 Hamas-led attack on Israel",
    "hamas attack israel 2023":      "2023 Hamas-led attack on Israel",
    "hamas attack israel":           "2023 Hamas-led attack on Israel",
    "hamas attacked israel":         "2023 Hamas-led attack on Israel",
    "hamas killed israelis":         "2023 Hamas-led attack on Israel",
    "israel hamas war":              "2023 Hamas-led attack on Israel",
    "hamas israel":                  "2023 Hamas-led attack on Israel",
    "israel hamas":                  "Israeli–Palestinian conflict",
    "israel palestine":              "Israeli–Palestinian conflict",
    "gaza":                          "Gaza Strip",
    "nasa 15 days darkness":         "NASA",
    "15 days darkness nasa":         "NASA",
    "15 days of darkness":           "NASA",
    "nasa darkness":                 "NASA",
    "15 days dark":                  "NASA",
    "nasa":                          "NASA",
    "apollo moon landing fake":      "Apollo 11",
    "apollo moon landing faked":     "Apollo 11",
    "moon landing faked stanley":    "Apollo 11",
    "kubrick moon landing":          "Apollo 11",
    "apollo kubrick":                "Apollo 11",
    "moon landing fake":             "Moon landing conspiracy theories",
    "moon landing hoax":             "Moon landing conspiracy theories",
    "nasa faked moon":               "Moon landing conspiracy theories",
    "moon landing conspiracy":       "Moon landing conspiracy theories",
    "moon landing":                  "Apollo program",
    "apollo program":                "Apollo program",
    "apollo 11":                     "Apollo 11",
    "chandrayaan-3 moon landing":    "Chandrayaan-3",
    "chandrayaan 3 landing":         "Chandrayaan-3",
    "chandrayaan-3":                 "Chandrayaan-3",
    "chandrayaan 3":                 "Chandrayaan-3",
    "isro moon mission":             "Chandrayaan-3",
    "isro chandrayaan":              "Chandrayaan-3",
    "chandrayaan":                   "Chandrayaan programme",
    "gaganyaan":                     "Gaganyaan",
    "isro":                          "Indian Space Research Organisation",
    "india won icc t20 world cup 2024":   "2024 ICC Men's T20 World Cup",
    "india t20 world cup 2024":           "2024 ICC Men's T20 World Cup",
    "india beat south africa t20":        "2024 ICC Men's T20 World Cup",
    "t20 world cup 2024":                 "2024 ICC Men's T20 World Cup",
    "icc t20 world cup 2024":             "2024 ICC Men's T20 World Cup",
    "t20 world cup":                      "ICC Men's T20 World Cup",
    "icc t20 world cup":                  "ICC Men's T20 World Cup",
    "virat kohli world cup":              "2024 ICC Men's T20 World Cup",
    "rohit sharma world cup":             "2024 ICC Men's T20 World Cup",
    "virat kohli":                        "Virat Kohli",
    "rohit sharma":                       "Rohit Sharma",
    "ms dhoni":                           "MS Dhoni",
    "ipl":                                "Indian Premier League",
    "apple record revenue":          "Apple Inc.",
    "apple quarterly revenue":       "Apple Inc.",
    "apple iphone revenue":          "Apple Inc.",
    "apple revenue":                 "Apple Inc.",
    "apple intelligence":            "Apple Inc.",
    "apple iphone 16":               "IPhone 16",
    "iphone 16":                     "IPhone 16",
    "apple":                         "Apple Inc.",
    "iphone":                        "IPhone",
    "deepmind alphafold protein":    "AlphaFold",
    "alphafold protein folding":     "AlphaFold",
    "alphafold":                     "AlphaFold",
    "deepmind":                      "Google DeepMind",
    "google deepmind":               "Google DeepMind",
    "protein folding":               "Protein folding",
    "5g towers spread covid":        "5G conspiracy theories",
    "5g causes coronavirus":         "5G conspiracy theories",
    "5g towers covid":               "5G conspiracy theories",
    "5g covid":                      "5G conspiracy theories",
    "5g radiation coronavirus":      "5G conspiracy theories",
    "5g टावर कोरोना":                "5G conspiracy theories",
    "vaccine microchip bill gates":  "COVID-19 vaccine misinformation",
    "covid vaccine microchip":       "COVID-19 vaccine misinformation",
    "vaccine contains microchip":    "COVID-19 vaccine misinformation",
    "vaccine tracking chip":         "COVID-19 vaccine misinformation",
    "vaccine microchip":             "COVID-19 vaccine misinformation",
    "bleach cures covid":            "COVID-19 misinformation",
    "drinking bleach coronavirus":   "COVID-19 misinformation",
    "bleach kills coronavirus":      "COVID-19 misinformation",
    "disinfectant cure covid":       "COVID-19 misinformation",
    "bleach covid":                  "COVID-19 misinformation",
    "covid pandemic":                "COVID-19 pandemic",
    "coronavirus pandemic":          "COVID-19 pandemic",
    "covid":                         "COVID-19 pandemic",
    "coronavirus":                   "COVID-19 pandemic",
    "narendra modi":                 "Narendra Modi",
    "prime minister modi":           "Narendra Modi",
    "pm modi":                       "Narendra Modi",
    "modi":                          "Narendra Modi",
    "rahul gandhi":                  "Rahul Gandhi",
    "bjp":                           "Bharatiya Janata Party",
    "parliament india":              "Parliament of India",
    "lok sabha":                     "Lok Sabha",
    "bill gates":                    "Bill Gates",
    "google":                        "Google",
    "microsoft":                     "Microsoft",
    "space station iss":             "International Space Station",
    "james webb telescope":          "James Webb Space Telescope",
    "james webb":                    "James Webb Space Telescope",
    "deep state":                    "Deep state conspiracy theory",
    "george soros":                  "George Soros",
    "soros":                         "George Soros",
    "illuminati":                    "Illuminati",
    "new world order":               "New World Order (conspiracy theory)",
    "climate change is a hoax":      "Climate change denial",
    "global warming hoax":           "Climate change denial",
    "climate change hoax":           "Climate change denial",
    "climate change":                "Climate change",
    "global warming":                "Global warming",
    "flat earth":                    "Flat Earth",
    "chemtrail":                     "Chemtrail conspiracy theory",
    "reptilian":                     "Reptilian conspiracy theory",
    "qanon":                         "QAnon",
    "mpox":                          "Mpox",
    "obama":                         "Barack Obama",
    "चंद्रयान":                     "Chandrayaan-3",
    "इसरो":                          "Indian Space Research Organisation",
    "गगनयान":                        "Gaganyaan",
    "विश्व कप 2024":                 "2024 ICC Men's T20 World Cup",
    "t20 विश्व":                     "2024 ICC Men's T20 World Cup",
    "विश्व कप":                      "ICC Men's T20 World Cup",
    "रोहित शर्मा":                   "Rohit Sharma",
    "विराट कोहली":                   "Virat Kohli",
    "5g टावर से कोरोना":             "5G conspiracy theories",
    "5g टावर":                       "5G conspiracy theories",
    "5g tower":                      "5G conspiracy theories",
    "कोरोना वायरस":                  "COVID-19 pandemic",
    "वैक्सीन माइक्रोचिप":           "COVID-19 vaccine misinformation",
    "माइक्रोचिप":                    "COVID-19 vaccine misinformation",
    "मोदी":                          "Narendra Modi",
    "संसद":                          "Parliament of India",
    "सुप्रीम कोर्ट":                "Supreme Court of India",
}

STOPWORDS_EN = {
    "this","that","with","have","from","they","will","been","were","what",
    "when","where","which","their","there","about","would","could","should",
    "into","than","then","also","just","more","some","such","only","even",
    "most","other","over","same","very","news","says","said","like","make",
    "know","time","year","people","good","great","need","want","come","back",
    "does","doing","done","your","mine","ours","them","these","those","here",
    "really","secret","shocking","hidden","breaking","alert","exposed",
    "the","and","for","are","but","not","you","all","can","her","was",
    "one","our","out","had","him","his","how","its","now","get","has",
    "new","any","two","way","use","may","day","got","let","put","too",
    "old","see","set","big","act","add","age","ago","air","lot","own",
    "per","run","yet","six","ten","far","few","end","off","try","why",
    "government","hiding","public","dangerous","fact","truth","media",
    "world","claim","report","according","between","through","after",
    "before","during","without","within","against","because","while",
    "says","said","report","reports","reported","according",
    "a","an","in","on","at","to","of","is","it","be","as","by","or",
    "do","if","up","so","no","we","my","he","me","us","secretly",
    "launch","launched","announced","confirmed","major","latest",
    # ── additional weak/generic single words that pollute queries ──
    "war","world","won","win","wins","beat","beats","beats","south",
    "north","east","west","country","nation","state","city","new",
    "election","electoral","college","vote","voting","votes","voters",
    "presidential","president","prime","minister","minister","cup",
    "final","finals","match","game","games","team","teams","player",
    "players","says","said","tells","told","man","men","woman","women",
    "first","last","next","top","best","big","biggest","huge","massive",
}


# ═══════════════════════════════════════════════════════════════════════════════
# ── SECTION: EVENT DETECTION (NEW) ────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── Event-type detection rules — ordered from most-specific to least ──────────
# Each rule: (regex, event_type, wiki_template_fn)
# wiki_template_fn receives (text_lower, year_str) → str wiki topic

EVENT_TYPE_RULES = [
    # ── US / Global elections ──────────────────────────────────────────────
    (r'\b(us|u\.s\.|united states|american|presidential)\s+(election|elections|vote|voting)\b',
     "us_election",
     lambda t, y: f"2024 United States presidential election" if "2024" in t else
                  f"United States presidential election"),

    (r'\b(election|elections|vote|voting)\b.{0,40}\b(us|u\.s\.|united states|america|american|trump|biden|harris|kamala)\b',
     "us_election",
     lambda t, y: "2024 United States presidential election" if "2024" in t else
                  "United States presidential election"),

    (r'\b(trump|biden|harris|kamala).{0,40}\b(election|elected|wins|won|president|presidential)\b',
     "us_election",
     lambda t, y: "2024 United States presidential election"),

    # ── Indian elections ───────────────────────────────────────────────────
    (r'\b(india|indian|lok sabha|assembly)\s+(election|elections|vote|voting)\b',
     "india_election",
     lambda t, y: "2024 Indian general election" if "2024" in t else "Elections in India"),

    # ── Cricket / T20 / IPL ────────────────────────────────────────────────
    (r'\b(t20|twenty20|icc|ipl)\s+(world cup|wc|championship)\b',
     "cricket_t20wc",
     lambda t, y: "2024 ICC Men's T20 World Cup" if "2024" in t else "ICC Men's T20 World Cup"),

    (r'\b(world cup|wc)\b.{0,30}\b(t20|twenty20|cricket|india|rohit|kohli)\b',
     "cricket_t20wc",
     lambda t, y: "2024 ICC Men's T20 World Cup" if "2024" in t else "ICC Men's T20 World Cup"),

    (r'\b(india|rohit sharma|rohit|virat kohli|virat|kohli).{0,40}\b(world cup|wc|t20|icc)\b',
     "cricket_t20wc",
     lambda t, y: "2024 ICC Men's T20 World Cup" if "2024" in t else "ICC Men's T20 World Cup"),

    (r'\bipl\b',
     "cricket_ipl",
     lambda t, y: "Indian Premier League"),

    # ── Russia-Ukraine war ─────────────────────────────────────────────────
    # Note: use word-safe patterns — avoid \b before prefix stems like 'invad'
    (r'\brussia\s+invade[sd]?\s+ukraine|\brussia[n]?\s+invasion\s+of\s+ukraine',
     "russia_ukraine",
     lambda t, y: "2022 Russian invasion of Ukraine"),

    (r'\b(?:russia|russian|ukraine|ukrainian|putin|zelensky|zelenskyy)\b.{0,50}\b(?:war|invasion|conflict|troops|missile|offensive)\b',
     "russia_ukraine",
     lambda t, y: "2022 Russian invasion of Ukraine"),

    (r'\brussia\s+launch\w*\s+\w+\s+ukraine|\b(?:invasion|war)\b.{0,30}\b(?:russia|ukraine)\b',
     "russia_ukraine",
     lambda t, y: "2022 Russian invasion of Ukraine"),

    # ── Hamas/Israel ───────────────────────────────────────────────────────
    (r'\bhamas\s+attack\w*\b',
     "hamas_israel",
     lambda t, y: "2023 Hamas-led attack on Israel"),

    (r'\b(?:hamas|israel|israeli|gaza|palestine|palestinian)\b.{0,40}\b(?:attack(?:ed)?|war|killed|conflict|bomb(?:ed)?|missile)\b',
     "hamas_israel",
     lambda t, y: "2023 Hamas-led attack on Israel"),

    (r'\boctober\s+7\b',
     "hamas_israel",
     lambda t, y: "2023 Hamas-led attack on Israel"),

    # ── ISRO / Chandrayaan / Space missions ───────────────────────────────
    (r'\b(chandrayaan|gaganyaan|aditya.?l1|pslv|gslv|isro)\b',
     "isro_mission",
     lambda t, y: "Chandrayaan-3" if "chandrayaan" in t else
                  "Gaganyaan" if "gaganyaan" in t else
                  "Indian Space Research Organisation"),

    (r'\b(moon landing|lunar landing|moon mission).{0,30}\b(isro|india|chandrayaan)\b',
     "isro_mission",
     lambda t, y: "Chandrayaan-3"),

    # ── NASA / Space (generic) ─────────────────────────────────────────────
    (r'\b(nasa|james webb|hubble|spacex|artemis|iss|space station)\b',
     "nasa_space",
     lambda t, y: "NASA" if "nasa" in t else
                  "James Webb Space Telescope" if "james webb" in t or "webb" in t else
                  "SpaceX" if "spacex" in t else "NASA"),

    # ── Moon landing conspiracy ────────────────────────────────────────────
    (r'\b(moon landing).{0,30}\b(fake|faked|hoax|kubrick|conspiracy)\b',
     "moon_conspiracy",
     lambda t, y: "Moon landing conspiracy theories"),

    # ── OpenAI / ChatGPT ──────────────────────────────────────────────────
    (r'\b(chatgpt|openai|gpt.?4|gpt.?3|sam altman)\b',
     "openai",
     lambda t, y: "ChatGPT" if "chatgpt" in t else
                  "Sam Altman" if "sam altman" in t else "OpenAI"),

    # ── Elon Musk / Twitter/X ─────────────────────────────────────────────
    (r'\b(elon musk|twitter|x\.com).{0,30}\b(bought|acquired|acquisition|renamed|rebranded)\b',
     "musk_twitter",
     lambda t, y: "Acquisition of Twitter by Elon Musk"),

    # ── COVID / vaccines ──────────────────────────────────────────────────
    (r'\b(covid|coronavirus|sars.?cov|pandemic).{0,40}\b(vaccine|microchip|5g|spread|origin)\b',
     "covid_misinfo",
     lambda t, y: "COVID-19 vaccine misinformation" if any(w in t for w in ["vaccine","microchip","chip"]) else
                  "5G conspiracy theories" if "5g" in t else "COVID-19 pandemic"),

    (r'\b(vaccine|vaccination).{0,30}\b(microchip|chip|bill gates|tracking|5g)\b',
     "vaccine_misinfo",
     lambda t, y: "COVID-19 vaccine misinformation"),

    # ── Climate change ────────────────────────────────────────────────────
    (r'\b(climate change|global warming).{0,30}\b(hoax|fake|real|denial|scientific)\b',
     "climate",
     lambda t, y: "Climate change denial" if any(w in t for w in ["hoax","fake","denial"]) else "Climate change"),

    # ── Flat earth / conspiracy ───────────────────────────────────────────
    (r'\b(flat earth|earth is flat)\b',
     "flat_earth",
     lambda t, y: "Flat Earth"),

    (r'\b(illuminati|new world order|deep state|chemtrail|reptilian|qanon)\b',
     "conspiracy",
     lambda t, y: "Illuminati" if "illuminati" in t else
                  "New World Order (conspiracy theory)" if "new world order" in t else
                  "Deep state conspiracy theory" if "deep state" in t else
                  "Chemtrail conspiracy theory" if "chemtrail" in t else
                  "Reptilian conspiracy theory" if "reptilian" in t else "QAnon"),

    # ── AlphaFold / DeepMind ──────────────────────────────────────────────
    (r'\b(alphafold|deepmind|protein folding)\b',
     "science",
     lambda t, y: "AlphaFold" if "alphafold" in t else "Google DeepMind"),

    # ── Apple / iPhone ────────────────────────────────────────────────────
    (r'\b(apple|iphone).{0,30}\b(revenue|record|launched|release|iphone 1[5-9])\b',
     "apple",
     lambda t, y: "Apple Inc."),

    # ── India politics ────────────────────────────────────────────────────
    (r'\b(narendra modi|pm modi|prime minister india|bjp|congress india|lok sabha|parliament india)\b',
     "india_politics",
     lambda t, y: "Narendra Modi" if any(w in t for w in ["modi","narendra"]) else
                  "Bharatiya Janata Party" if "bjp" in t else
                  "Parliament of India"),

    # ── NEW: Hindi-script direct event detection ──────────────────────────
    # These fire when the text is in Hindi and contains these exact sequences
    # ORDER MATTERS: more specific rules must come before generic ones

    (r'(वैक्सीन|टीका).{0,30}(माइक्रोचिप|चिप|बिल गेट्स)',
     "vaccine_misinfo",
     lambda t, y: "COVID-19 vaccine misinformation"),

    (r'(5g|5जी).{0,20}(कोरोना|covid|वायरस)',
     "5g_misinfo",
     lambda t, y: "5G conspiracy theories"),

    (r'(चंद्रयान|गगनयान|इसरो)',
     "isro_mission",
     lambda t, y: "Chandrayaan-3" if "चंद्रयान" in t else
                  "Gaganyaan" if "गगनयान" in t else
                  "Indian Space Research Organisation"),

    (r'(विश्व कप|t20 विश्व|आईसीसी)',
     "cricket_t20wc",
     lambda t, y: "2024 ICC Men's T20 World Cup" if "2024" in t else "ICC Men's T20 World Cup"),

    (r'(रोहित शर्मा|विराट कोहली|धोनी)',
     "cricket_t20wc",
     lambda t, y: "2024 ICC Men's T20 World Cup"),

    (r'(रूस|यूक्रेन|पुतिन)',
     "russia_ukraine",
     lambda t, y: "2022 Russian invasion of Ukraine"),

    (r'(चुनाव|election).{0,20}(भारत|india)',
     "india_election",
     lambda t, y: "2024 Indian general election" if "2024" in t else "Elections in India"),

    (r'(मोदी|भाजपा|बीजेपी|संसद|लोकसभा)',
     "india_politics",
     lambda t, y: "Narendra Modi" if "मोदी" in t else
                  "Bharatiya Janata Party" if any(w in t for w in ["भाजपा","बीजेपी"]) else
                  "Parliament of India"),

    (r'(कोरोना|कोविड|covid).{0,30}(वायरस|pandemic|महामारी)',
     "covid",
     lambda t, y: "COVID-19 pandemic"),

    (r'(चपटी पृथ्वी|flat earth)',
     "flat_earth",
     lambda t, y: "Flat Earth"),

    (r'(इलुमिनाती|illuminati)',
     "conspiracy",
     lambda t, y: "Illuminati"),
]


def detect_event_type(text):
    """
    NEW: Detect what real-world event category the text refers to.
    Returns (event_type, wiki_topic) or (None, None).
    Tries rules in order — most specific first.
    """
    t = text.lower()
    # Extract any 4-digit year present
    year_match = re.search(r'\b(20\d{2})\b', t)
    year_str = year_match.group(1) if year_match else ""

    for pattern, etype, wiki_fn in EVENT_TYPE_RULES:
        if re.search(pattern, t):
            topic = wiki_fn(t, year_str)
            return etype, topic

    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# ── SECTION: IMPROVED KEYWORD EXTRACTION (REWRITTEN) ─────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── Known named entities / organizations / people ────────────────────────────
KNOWN_ENTITIES = [
    # People
    "Donald Trump", "Joe Biden", "Kamala Harris", "Barack Obama",
    "Narendra Modi", "PM Modi", "Rahul Gandhi", "Arvind Kejriwal",
    "Vladimir Putin", "Volodymyr Zelensky", "Volodymyr Zelenskyy",
    "Elon Musk", "Bill Gates", "Mark Zuckerberg", "Sam Altman",
    "Rohit Sharma", "Virat Kohli", "MS Dhoni", "Sachin Tendulkar",
    "George Soros", "Jeff Bezos", "Sundar Pichai",
    # Orgs
    "ISRO", "NASA", "WHO", "UN", "FBI", "CIA", "IMF", "WTO",
    "OpenAI", "ChatGPT", "DeepMind", "AlphaFold",
    "Apple", "Google", "Microsoft", "Meta", "Tesla", "Twitter",
    "BJP", "Congress", "BBC", "Reuters", "CNN", "NDTV",
    "ICC", "BCCI", "IPL",
    # Events/places
    "T20 World Cup", "ICC World Cup", "IPL", "Champions Trophy",
    "Chandrayaan", "Gaganyaan", "Aditya-L1",
    "Ukraine", "Russia", "Gaza", "Israel", "Palestine",
    "Parliament", "Supreme Court", "Lok Sabha", "Rajya Sabha",
    "White House", "Capitol Hill",
]

# Compile entity patterns (longest first to prefer multi-word matches)
_ENTITY_PATTERNS = sorted(
    [(e, re.compile(r'\b' + re.escape(e) + r'\b', re.IGNORECASE)) for e in KNOWN_ENTITIES],
    key=lambda x: -len(x[0])
)

# ── Event phrase patterns — multi-word, high-signal phrases ──────────────────
EVENT_PHRASE_PATTERNS = [
    # Elections
    r'\b(?:us|u\.s\.|united states|american|presidential)\s+election(?:s)?\s*(?:20\d{2})?\b',
    r'\b(?:lok sabha|assembly|india[n]?)\s+election(?:s)?\s*(?:20\d{2})?\b',
    r'\b20\d{2}\s+(?:us|indian|presidential|general)\s+election(?:s)?\b',
    # Cricket events
    r'\b(?:icc\s+)?t20\s+world\s+cup(?:\s+20\d{2})?\b',
    r'\b(?:icc\s+)?(?:cricket\s+)?world\s+cup(?:\s+20\d{2})?\b',
    r'\b(?:india|england|australia)\s+(?:vs?\.?|versus)\s+(?:india|england|australia|south africa|pakistan|new zealand)\b',
    r'\bipl\s+(?:20\d{2}|season\s+\d+|final)?\b',
    # Wars / conflicts
    r'\brussia[n]?\s+invasion\s+of\s+ukraine\b',
    r'\brussia[n]?\s*[-–]\s*ukraine\s+war\b',
    r'\brussia\s+(?:invades?|invaded|launches?)\s+ukraine\b',
    r'\bhamas\s+attack(?:ed|s)?\s+(?:on\s+)?israel\b',
    r'\boctober\s+7\s+(?:attack|massacre|hamas)\b',
    # Space missions
    r'\bchandrayaan[- ]?(?:3|three|2|two|1|one)?\b',
    r'\bgaganyaan\s+(?:mission|launch|crew)?\b',
    r'\bisro\s+(?:launch(?:es?|ed)?|mission|satellite)\b',
    r'\bnasa\s+(?:artemis|james\s+webb|moon|mars|launch)\b',
    # AI/Tech
    r'\b(?:openai|chatgpt)\s+(?:users?|weekly|monthly|launch(?:es?)?\b)',
    r'\belon\s+musk\s+(?:buys?|bought|acquires?|acquired|twitter|x\.com)\b',
    r'\bapple\s+(?:iphone\s+\d+|revenue|record|launch(?:es?|ed)?)\b',
    # Moon landing conspiracy
    r'\bmoon\s+landing\s+(?:fake|faked|hoax|conspiracy)\b',
    r'\bapollo\s+(?:11|program)\s+(?:fake|faked|hoax)?\b',
    # COVID / vaccine conspiracy
    r'\bvaccine\s+(?:microchip|chip|tracking|5g|bill\s+gates)\b',
    r'\b5g\s+(?:towers?|network)\s+(?:spread|cause[sd]?|linked)\s+(?:covid|coronavirus|cancer)\b',
    r'\bcovid\s*[-–]?\s*19\s+(?:pandemic|vaccine|origin|lab)\b',
]

_EVENT_PHRASE_COMPILED = [re.compile(p, re.IGNORECASE) for p in EVENT_PHRASE_PATTERNS]


def extract_event_phrases(text):
    """
    NEW: Extract multi-word event phrases from text.
    These are high-signal, context-rich phrases.
    Returns list of matched phrase strings, deduped, longest first.
    """
    found = []
    seen = set()
    for pat in _EVENT_PHRASE_COMPILED:
        m = pat.search(text)
        if m:
            phrase = m.group(0).strip()
            pl = phrase.lower()
            if pl not in seen:
                seen.add(pl)
                found.append(phrase)
    # Sort longest first
    return sorted(found, key=len, reverse=True)


def extract_known_entities(text):
    """
    NEW: Extract known named entities from text using compiled regex patterns.
    Returns list of matched entity strings, deduped, longest first.
    """
    found = []
    seen = set()
    for entity, pat in _ENTITY_PATTERNS:
        if pat.search(text):
            el = entity.lower()
            if el not in seen:
                seen.add(el)
                found.append(entity)
    return found


def extract_named_entities(text):
    """
    IMPROVED: Extract named entities with better filtering.
    Combines known-entity matching, capitalized proper noun detection,
    and Hindi sequence extraction.
    """
    # Start with known entities (high confidence)
    entities = extract_known_entities(text)

    # Add capitalized proper nouns not already covered
    en_caps = re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,3})\b', text)
    seen = set(e.lower() for e in entities)
    for e in en_caps:
        el = e.lower()
        if el not in seen and el not in STOPWORDS_EN and len(el) > 3:
            # Skip single generic words that slipped through capitalization
            words = el.split()
            if len(words) > 1 or (len(words) == 1 and len(words[0]) > 5):
                seen.add(el)
                entities.append(e)

    # Hindi named entity sequences
    hi_seqs = re.findall(r'[\u0900-\u097F]+(?:\s+[\u0900-\u097F]+)*', text)
    for seq in hi_seqs:
        if len(seq) > 3:
            entities.append(seq)

    # Dedup preserving order, longest first
    seen2 = set()
    result = []
    for e in sorted(entities, key=len, reverse=True):
        el = e.lower()
        if el not in seen2:
            seen2.add(el)
            result.append(e)
    return result


def extract_year(text):
    """Extract a 4-digit year from text if present."""
    m = re.search(r'\b(20\d{2}|19\d{2})\b', text)
    return m.group(1) if m else ""


def extract_keywords(text):
    """
    IMPROVED: Context-aware keyword extraction.
    Priority: event phrases > known entities > capitalized nouns > filtered single words.
    Returns 3-8 high-quality keywords.
    """
    # 1. Event phrases (highest priority — multi-word, high signal)
    event_phrases = extract_event_phrases(text)

    # 2. Known entities
    entities = extract_known_entities(text)

    # 3. Capitalized proper nouns (not already captured)
    caps_nouns = []
    seen = set(e.lower() for e in event_phrases + entities)
    for e in re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b', text):
        el = e.lower()
        if el not in seen and el not in STOPWORDS_EN and len(el) > 3:
            seen.add(el)
            caps_nouns.append(e)

    # 4. Year if present
    year = extract_year(text)

    # 5. Hindi words
    hi_words = re.findall(r'[\u0900-\u097F]{3,}', text)
    stopwords_hi = {
        "और","में","की","के","को","से","है","हैं","था","थी","थे","कि","यह",
        "वह","इस","उस","जो","पर","भी","तो","हो","ने","एक","एवं","लेकिन",
    }
    hi_filtered = [w for w in hi_words if w not in stopwords_hi][:4]

    # 6. Fallback: meaningful single English words
    single_words = []
    all_seen = set(e.lower() for e in event_phrases + entities + caps_nouns)
    for w in re.findall(r'\b[a-zA-Z]{4,}\b', text):
        wl = w.lower()
        if wl not in STOPWORDS_EN and wl not in all_seen and len(wl) > 3:
            all_seen.add(wl)
            single_words.append(w)

    # Combine in priority order
    combined = event_phrases[:2] + entities[:3] + caps_nouns[:2]
    if year and year not in " ".join(combined):
        combined.append(year)
    combined += hi_filtered
    combined += single_words[:max(0, 6 - len(combined))]

    # Dedup final list
    seen_final = set()
    result = []
    for kw in combined:
        kl = kw.lower().strip()
        if kl and kl not in seen_final:
            seen_final.add(kl)
            result.append(kw)

    return result[:10]


def extract_newsapi_keywords(text):
    """
    Build precise, context-aware keywords for NewsAPI queries.
    Priority: named persons → named orgs → year → event phrase → event wiki terms.
    Returns a list of up to 5 terms ordered by relevance signal strength.
    """
    # For Hindi text: translate first, then extract from translated version too
    hi_translated = ""
    if is_hindi(text):
        hi_kw = _hindi_to_english_keywords(text)
        hi_translated = " ".join(hi_kw)

    # Run event detection on both original and translated text
    work_text       = hi_translated if hi_translated else text
    year            = extract_year(text) or extract_year(hi_translated)
    _, event_wiki   = detect_event_type(work_text) or detect_event_type(text)
    entities        = extract_known_entities(work_text) or extract_known_entities(text)
    event_phrases   = extract_event_phrases(work_text) or extract_event_phrases(text)

    _people_set    = {x.lower() for x in KNOWN_ENTITIES[:22]}
    persons        = [e for e in entities if e.lower() in _people_set]
    orgs           = [e for e in entities if e.lower() not in _people_set]

    def _first_pos(lst, ref_text):
        tl = ref_text.lower()
        best_e, best_p = None, len(ref_text) + 1
        for e in lst:
            p = tl.find(e.lower())
            if p != -1 and p < best_p:
                best_p = p; best_e = e
        return best_e

    primary_person = _first_pos(persons, work_text)
    primary_org    = _first_pos(orgs, work_text)
    second_person  = next((e for e in persons if e != primary_person), None)
    second_org     = next((e for e in orgs   if e != primary_org),    None)

    parts = []
    seen  = set()

    def _add(s):
        sl = (s or "").strip().lower()
        if sl and sl not in seen and len(sl) > 1:
            seen.add(sl); parts.append(s.strip())

    _add(primary_person)   # e.g. "Donald Trump"
    _add(second_person)    # e.g. "Kamala Harris"
    _add(primary_org)      # e.g. "ISRO", "Ukraine"
    _add(second_org)       # e.g. "Russia"
    _add(year)             # e.g. "2024"

    # Best event phrase — deduplicated against already-added parts
    # Only add if it contributes NEW words not already in parts
    if event_phrases:
        ep = event_phrases[0]
        ep_words = ep.split()[:4]
        new_ep_words = [w for w in ep_words if w.lower() not in seen]
        if new_ep_words:
            frag = " ".join(new_ep_words)
            _add(frag)

    # Event wiki key terms as fallback
    if not event_phrases and event_wiki:
        skip_ew = {"the","a","an","of","by","in","on","at","and","or",
                   "conspiracy","misinformation","theories","theory","denial"}
        ew_words = [w for w in event_wiki.split() if w.lower() not in skip_ew][:3]
        for w in ew_words:
            _add(w)

    # Final fallback: meaningful single words from the text
    if len(parts) < 2:
        for w in re.findall(r'\b[a-zA-Z]{4,}\b', work_text or text):
            if len(parts) >= 4:
                break
            wl = w.lower()
            if wl not in STOPWORDS_EN and wl not in seen:
                seen.add(wl); parts.append(w)

    return parts[:5]


# ═══════════════════════════════════════════════════════════════════════════════
# ── SECTION: IMPROVED WIKIPEDIA TOPIC RESOLUTION (REWRITTEN) ─────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_wiki_topic(claim_keywords, text):
    """
    UPDATED: Strict priority — EVENT first, then long phrase, then first-mentioned entity.

    Priority order:
    1. detect_event_type() — always wins if it fires (event > any entity)
    2. Hindi WIKI_TOPIC_MAP keys — direct script match
    3. WIKI_TOPIC_MAP phrase matching — scored by LENGTH (longer = more specific)
       Multi-word phrases (≥3 words) beat single words by design.
       Only the FIRST-MENTIONED entity in text is used when multiple exist.
    4. Known entity fallback (first-mentioned only)
    5. Keyword fallback
    Returns "" only when nothing at all matches (caller should add its own fallback).
    """
    text_lower = text.lower()

    # ── Step 1: Event detection — ALWAYS wins ────────────────────────────
    # This covers: US election, T20 World Cup, Russia-Ukraine, ISRO, etc.
    event_type, event_wiki = detect_event_type(text)
    if event_wiki:
        return event_wiki

    # ── Step 2: Hindi WIKI_TOPIC_MAP keys — direct Unicode match ─────────
    text_clean    = re.sub(r'[^\w\s]', ' ', text_lower)
    words_in_text = set(text_clean.split())

    best_topic = ""
    best_score = 0.0

    for key, topic in WIKI_TOPIC_MAP.items():
        if any('\u0900' <= c <= '\u097F' for c in key):
            if key in text:
                score = 2.0 + len(key) * 0.05   # Hindi direct match scores high
                if score > best_score:
                    best_score = score
                    best_topic = topic
            continue

        key_words = key.split()
        key_len   = len(key_words)

        matched = sum(1 for w in key_words if w in words_in_text)
        if matched == 0:
            continue

        match_ratio = matched / key_len

        # Stricter minimum: require higher coverage for longer keys
        min_ratio = 0.6 if key_len <= 2 else 0.7 if key_len <= 4 else 0.80
        if match_ratio < min_ratio:
            continue

        # ── Scoring: LENGTH is the dominant signal ────────────────────────
        # A 4-word key like "trump won 2024 election" must beat "kamala harris"
        # Formula:
        #   base          = match_ratio
        #   length_bonus  = key_len * 0.25   (longer → much higher)
        #   full_bonus    = 0.80 if all words matched
        #   single_penalty= -0.40 for single-word keys (too generic)
        length_bonus    = key_len * 0.25
        full_bonus      = 0.80 if match_ratio == 1.0 else 0.0
        single_penalty  = -0.40 if key_len == 1 else 0.0

        # ── First-mention bonus: prefer topics whose key words appear EARLY ─
        # Find position of first key word in text
        positions = [text_lower.find(w) for w in key_words if w in text_lower]
        if positions:
            earliest = min(p for p in positions if p >= 0)
            # Score boost for words near start of text (position 0–50 chars)
            position_bonus = max(0.0, (200 - earliest) / 200) * 0.30
        else:
            position_bonus = 0.0

        score = match_ratio + length_bonus + full_bonus + single_penalty + position_bonus

        if score > best_score:
            best_score = score
            best_topic = topic

    if best_topic:
        return best_topic

    # ── Step 3: First-mentioned entity only (not all entities) ───────────
    # "Trump wins election defeating Kamala Harris" → Trump is first → use Trump
    entities = extract_known_entities(text)
    if entities:
        def _pos(e):
            p = text_lower.find(e.lower())
            return p if p >= 0 else len(text)
        # Sort by position in text — take earliest mentioned
        entities_sorted = sorted(entities, key=_pos)
        for entity in entities_sorted[:2]:   # try top-2 by position
            el = entity.lower()
            if el in WIKI_TOPIC_MAP:
                return WIKI_TOPIC_MAP[el]
            for key, topic in WIKI_TOPIC_MAP.items():
                if el in key or key in el:
                    return topic

    # ── Step 4: Keyword fallback ──────────────────────────────────────────
    if claim_keywords:
        for kw in claim_keywords[:3]:
            kl = kw.lower()
            if kl in WIKI_TOPIC_MAP:
                return WIKI_TOPIC_MAP[kl]

    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# ── SECTION: TWITTER QUERY GENERATION (NEW) ───────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def generate_twitter_query(text, all_keywords, prediction):
    """
    Generates 5 focused, deduplicated, news-specific Twitter search queries.

    Strategy:
    1. Use detect_event_type() to identify the event category first
    2. Extract ALL relevant entities + year from the text
    3. Build 5 distinct queries covering: primary person, event phrase,
       person+year, event+year, and a broad topic fallback
    4. For FAKE NEWS: mix in "fact check" / "debunked" variants
    5. Deduplicate words within each query
    6. Never use generic words like "war", "world", "election" alone
    """
    t_lower = text.lower()
    year    = extract_year(text)

    # ── Step 1: Event detection ───────────────────────────────────────────
    event_type, event_wiki = detect_event_type(text)

    # ── Step 2: Extract all entities and event phrases ────────────────────
    all_entities   = extract_known_entities(text)
    event_phrases  = extract_event_phrases(text)

    # Separate people from orgs/places — use full KNOWN_ENTITIES index
    _people_set = {x.lower() for x in KNOWN_ENTITIES[:22]}  # index 0-21 = people
    persons    = [e for e in all_entities if e.lower() in _people_set]
    orgs       = [e for e in all_entities if e.lower() not in _people_set]

    # ── Step 3: Determine PRIMARY subject and EVENT CONTEXT ───────────────
    # Primary = the most important person/entity in the story
    # For "Trump wins election", primary = "Donald Trump", NOT "Kamala Harris"
    # Strategy: pick the FIRST entity mentioned in text (subject position)

    def _first_mentioned(entity_list):
        """Return entity that appears earliest in the original text."""
        best_e, best_pos = None, len(text) + 1
        for e in entity_list:
            pos = t_lower.find(e.lower())
            if pos != -1 and pos < best_pos:
                best_pos = pos
                best_e = e
        return best_e

    primary_person = _first_mentioned(persons)   # e.g. "Donald Trump"
    primary_org    = _first_mentioned(orgs)       # e.g. "Ukraine", "ISRO"
    primary        = primary_person or primary_org or ""

    # Event context: best event phrase > event_wiki keywords > nothing
    event_ctx = ""
    if event_phrases:
        event_ctx = event_phrases[0]
    elif event_wiki:
        wiki_words = [w for w in event_wiki.split()
                      if w.lower() not in {"the","a","an","of","by","in","on","at","and","or"}]
        event_ctx = " ".join(wiki_words[:5])

    # When no known entity was found, fall back to event_wiki words as primary
    # e.g. "5G COVID-19 misinformation" → primary = "5G COVID-19"
    if not primary and event_wiki:
        wiki_words = [w for w in event_wiki.split()
                      if w.lower() not in {"the","a","an","of","by","in","on","at","and","or"}]
        primary = " ".join(wiki_words[:3])

    # When STILL no primary, pull top keyword from all_keywords
    if not primary and all_keywords:
        en_kw = [k for k in all_keywords if all(ord(c) < 128 for c in k)]
        primary = " ".join(en_kw[:2])

    # Second person/entity for paired queries (e.g. Putin + Zelensky, Trump + Harris)
    second_person = next((e for e in persons if e != primary_person), None)
    second_org    = next((e for e in orgs   if e != primary_org),    None)

    # ── Step 4: Build dedup helper ────────────────────────────────────────
    def _dedup(*parts):
        seen_w = set()
        out    = []
        for part in parts:
            if not part:
                continue
            for w in part.split():
                if w.lower() not in seen_w:
                    seen_w.add(w.lower())
                    out.append(w)
        return " ".join(out).strip()

    # ── Step 5: Build 5–7 distinct queries ───────────────────────────────
    raw_queries = []

    # Q1 — Most specific: primary subject + event context + year
    q1 = _dedup(primary, event_ctx, year)
    raw_queries.append(q1)

    # Q2 — Primary + year only (clean, shorter variant)
    if primary and year:
        raw_queries.append(_dedup(primary, year))
    elif primary and second_person:
        raw_queries.append(_dedup(primary, second_person))
    elif primary:
        raw_queries.append(primary)

    # Q3 — Event context + year (broader, no specific person)
    # Use event_wiki directly if it's more descriptive than event_ctx
    if event_wiki and year:
        q3_base = event_wiki[:50]
        raw_queries.append(_dedup(q3_base, year))
    elif event_ctx and year:
        raw_queries.append(_dedup(event_ctx, year))
    elif event_ctx:
        raw_queries.append(event_ctx)

    # Q4 — Second entity paired with event/year (different angle)
    if second_person:
        raw_queries.append(_dedup(second_person, event_ctx or year))
    elif second_org and second_org != primary_org:
        raw_queries.append(_dedup(second_org, year or event_ctx))
    elif primary_org and primary_person:
        raw_queries.append(_dedup(primary_org, year))

    # Q5/Q6 — Prediction-specific variants
    if prediction == "FAKE NEWS":
        raw_queries.append(_dedup(primary or event_ctx, "fact check"))
        raw_queries.append(_dedup(primary or event_ctx, "debunked misinformation"))
    else:
        # All entities together
        raw_queries.append(_dedup(primary, second_person or second_org or primary_org, year))
        # Full event wiki topic as its own search
        if event_wiki:
            raw_queries.append(event_wiki[:60])

    # ── Step 6: Deduplicate queries, remove empties, ensure minimum ───────
    seen_q  = set()
    queries = []
    for q in raw_queries:
        q = q.strip()
        if not q or len(q) < 4:
            continue
        ql = q.lower()
        if ql in seen_q:
            continue
        # Skip queries that are just a year alone
        if re.fullmatch(r"20\d{2}", q):
            continue
        seen_q.add(ql)
        queries.append(q)

    # Final fallback: use best all_keywords
    if not queries:
        kw_en = [k for k in all_keywords if all(ord(c) < 128 for c in k)][:4]
        queries = [_dedup(*kw_en[:3]), _dedup(*kw_en[:2])]
        queries = [q for q in queries if q]

    return queries[:7]  # Return up to 7 for Nitter to use




# ═══════════════════════════════════════════════════════════════════════════════
# ── SECTION: CORE SCORING ENGINE (unchanged) ──────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def score_claim(text):
    text_lower = text.lower()
    text_clean = re.sub(r'[^\w\s]', ' ', text_lower)

    kb_real = check_verified_event(text)
    kb_fake = check_misinformation_kb(text)

    if kb_real and kb_real[2] >= 0.75:
        real_score = min(95, 70 + round(kb_real[2] * 25))
        return "REAL NEWS", 100 - real_score, real_score, kb_real, kb_fake

    if kb_fake and kb_fake[2] >= 0.75:
        fake_score = min(95, 70 + round(kb_fake[2] * 25))
        return "FAKE NEWS", fake_score, 100 - fake_score, kb_real, kb_fake

    strong_fake_hits = sum(1 for p in STRONG_FAKE_SIGNALS if re.search(p, text_lower))
    strong_real_hits = sum(1 for p in STRONG_REAL_SIGNALS if re.search(p, text_lower))

    suspicious_hits = sum(1 for p, _ in SUSPICIOUS_PATTERNS if re.search(p, text_lower))
    credible_hits   = sum(1 for p, _ in CREDIBLE_PATTERNS   if re.search(p, text_lower))

    fake_raw = 50
    fake_raw += strong_fake_hits * 22
    fake_raw -= strong_real_hits * 18
    fake_raw += suspicious_hits * 8
    fake_raw -= credible_hits * 7

    abs_claims = len(re.findall(r'\b(100%|proven|guaranteed|banned|suppressed|secret|exposed|shocking)\b', text_lower))
    fake_raw += abs_claims * 5

    caps_words = len(re.findall(r'\b[A-Z]{4,}\b', text))
    fake_raw += min(caps_words * 3, 15)

    word_count = len(text.split())
    if word_count < 8:
        fake_raw += 5

    fake_raw = max(5, min(95, fake_raw))
    real_raw = 100 - fake_raw

    label = "FAKE NEWS" if fake_raw > 50 else "REAL NEWS"

    if kb_real and kb_real[2] >= 0.6:
        if label == "FAKE NEWS" and fake_raw < 85:
            label = "REAL NEWS"
            real_raw = max(real_raw, 65)
            fake_raw = 100 - real_raw
    if kb_fake and kb_fake[2] >= 0.6:
        if label == "REAL NEWS" and real_raw < 85:
            label = "FAKE NEWS"
            fake_raw = max(fake_raw, 65)
            real_raw = 100 - fake_raw

    return label, fake_raw, real_raw, kb_real, kb_fake


# ─── Knowledge Base Matching ───────────────────────────────────────────────────

def check_verified_event(text):
    text_lower = text.lower()
    text_clean = re.sub(r'[^\w\s]', ' ', text_lower)
    best_match = None
    best_score = 0
    for phrase, label, description in VERIFIED_EVENTS_KB:
        words = phrase.split()
        matched = sum(1 for w in words if w in text_clean)
        score = matched / len(words)
        if score > best_score and score >= 0.6:
            best_score = score
            best_match = (description, label, score)
    return best_match

def check_misinformation_kb(text):
    text_lower = text.lower()
    text_clean = re.sub(r'[^\w\s]', ' ', text_lower)
    best_match = None
    best_score = 0
    for phrase, label, description in KNOWN_MISINFORMATION_KB:
        words = phrase.split()
        matched = sum(1 for w in words if w in text_clean)
        score = matched / len(words)
        if score > best_score and score >= 0.65:
            best_score = score
            best_match = (description, label, score)
    return best_match


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_source_favicon(source_name):
    if not source_name:
        return ""
    key = source_name.lower().strip()
    if key in SOURCE_FAVICONS:
        return SOURCE_FAVICONS[key]
    for known, fav in SOURCE_FAVICONS.items():
        if known in key or key in known:
            return fav
    domain = re.sub(r'[^a-z0-9]', '', key)
    return f"https://www.google.com/s2/favicons?domain={domain}.com&sz=32"

def get_source_initials(source_name):
    if not source_name:
        return "N"
    words = source_name.strip().split()
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


# ─── Thumbnail extraction helpers ─────────────────────────────────────────────

def _extract_og_image_from_html(html):
    SKIP_PATTERNS = [
        'logo', 'icon', 'sprite', 'favicon', 'avatar', 'placeholder',
        'blank', 'pixel', 'spacer', 'badge', 'button', 'profile',
        'default-image', 'no-image', 'noimage', 'missing',
    ]

    def _is_valid(src):
        if not src:
            return False
        src = src.strip()
        if not src.startswith(("http://", "https://", "//")):
            return False
        sl = src.lower()
        if sl.endswith(".svg") or ".svg?" in sl or ".svg#" in sl:
            return False
        if any(skip in sl for skip in SKIP_PATTERNS):
            return False
        dm = re.search(r'[/_\-](\d{1,3})x(\d{1,3})[/_\-.]', src)
        if dm:
            w, h = int(dm.group(1)), int(dm.group(2))
            if w < 200 or h < 150:
                return False
        return True

    try:
        _soup = BeautifulSoup(html, "html.parser")
        for _prop, _names in [
            ("og:image:secure_url", []),
            ("og:image",            []),
        ]:
            _tag = _soup.find("meta", property=_prop)
            if _tag and _is_valid((_tag.get("content") or "").strip()):
                return (_tag.get("content") or "").strip()
        for _name in ["twitter:image:src", "twitter:image"]:
            _tag = _soup.find("meta", attrs={"name": _name})
            if _tag and _is_valid((_tag.get("content") or "").strip()):
                return (_tag.get("content") or "").strip()
    except Exception:
        pass

    patterns = [
        r'property=["\']og:image:secure_url["\'][^>]{0,300}content=["\']([^"\']{10,})["\']',
        r'content=["\']([^"\']{10,})["\'][^>]{0,300}property=["\']og:image:secure_url["\']',
        r'property=["\']og:image["\'][^>]{0,300}content=["\']([^"\']{10,})["\']',
        r'content=["\']([^"\']{10,})["\'][^>]{0,300}property=["\']og:image["\']',
        r'name=["\']twitter:image:src["\'][^>]{0,300}content=["\']([^"\']{10,})["\']',
        r'content=["\']([^"\']{10,})["\'][^>]{0,300}name=["\']twitter:image:src["\']',
        r'name=["\']twitter:image["\'][^>]{0,300}content=["\']([^"\']{10,})["\']',
        r'content=["\']([^"\']{10,})["\'][^>]{0,300}name=["\']twitter:image["\']',
        r'itemprop=["\']image["\'][^>]{0,300}content=["\']([^"\']{10,})["\']',
        r'content=["\']([^"\']{10,})["\'][^>]{0,300}itemprop=["\']image["\']',
    ]

    for pat in patterns:
        m = re.search(pat, html, re.I | re.S)
        if m:
            src = m.group(1).strip()
            if _is_valid(src):
                return src

    try:
        soup = BeautifulSoup(html, "html.parser")
        container = (
            soup.find("article") or
            soup.find("main") or
            soup.find(id=re.compile(r"(content|article|story|body)", re.I)) or
            soup.find(class_=re.compile(r"(content|article|story|body)", re.I)) or
            soup.body or
            soup
        )
        ARTICLE_HINTS = ("news", "article", "media", "photo", "image",
                         "upload", "cdn", "content", "story", "img")
        best_candidate = ""
        for img in (container.find_all("img") if container else []):
            src = (img.get("src") or
                   img.get("data-src") or
                   img.get("data-lazy-src") or
                   img.get("data-original") or
                   (img.get("srcset") or "").split()[0])
            if not src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            if not _is_valid(src):
                continue
            try:
                w = int(img.get("width") or 0)
                h = int(img.get("height") or 0)
                if (w and w < 300) or (h and h < 200):
                    continue
            except (ValueError, TypeError):
                pass
            if any(hint in src.lower() for hint in ARTICLE_HINTS):
                return src
            if not best_candidate:
                best_candidate = src
        if best_candidate:
            return best_candidate
    except Exception:
        pass

    return ""


def _resolve_real_url(url):
    if not url or not url.startswith("http"):
        return url

    REDIRECT_HOSTS = ("news.google.com", "t.co", "bit.ly", "ow.ly",
                      "tinyurl.com", "buff.ly", "dlvr.it")
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        host = ""

    if not any(h in host for h in REDIRECT_HOSTS):
        return url

    cached = THUMBNAIL_CACHE.get("__url__" + url)
    if cached and (time.time() - cached["ts"]) < CACHE_EXPIRY_SECONDS:
        return cached["image"] or url

    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
            allow_redirects=True,
            timeout=5,
        )
        final = r.url
        if final and "news.google.com" not in final and final.startswith("http"):
            THUMBNAIL_CACHE["__url__" + url] = {"image": final, "ts": time.time()}
            return final
    except Exception:
        pass

    return url


_ICON_URL_FRAGMENTS = [
    "news.google.com",
    "google.com/s2",
    "lh3.googleusercontent.com",
    "lh4.googleusercontent.com",
    "lh5.googleusercontent.com",
    "lh6.googleusercontent.com",
    "encrypted-tbn",
    "news_icon",
    "app-icon",
    "apple-touch-icon",
    "social-icon",
    "site-icon",
    "brand-logo",
    "/icon",
    "/logo",
]

_GOOGLE_NEWS_ICON_SIGNATURES = [
    "CBMi",
    "w=48", "w=32", "w=16",
    "h=48", "h=32", "h=16",
    "size=48", "size=32", "size=16",
]


def _is_clean_image(url):
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url.startswith("http"):
        return False
    ul = url.lower()
    BAD = [
        "favicon", "sprite", "1x1", "pixel", "spacer",
        "tracking", "beacon", "news.google.com", "google.com/s2",
    ]
    if any(b in ul for b in BAD):
        return False
    return True


# Domains known to serve generic/unrelated stock images as OG images
# For these, we skip scraping and go straight to fallback
_STOCK_IMAGE_DOMAINS = {
    "sanskriiti.com", "sanskriti.com", "affairscloud.com",
    "gktoday.in", "currentaffairs.gktoday.in",
    "jagranjosh.com", "testbook.com", "byjus.com",
    "adda247.com", "oliveboard.in", "gradupgradation.com",
    "examsdaily.in", "bankersadda.com", "sscadda.com",
    "currentaffairs4u.com", "careermantra.net",
    # These serve random Unsplash/Pexels stock via CDN
    "images.unsplash.com", "cdn.pixabay.com",
    "stock.adobe.com", "shutterstock.com", "istockphoto.com",
    "gettyimages.com",
}

# URL path fragments that strongly indicate a stock/generic image
_STOCK_URL_FRAGMENTS = [
    "unsplash.com/photo", "pixabay.com/photos",
    "pexels.com/photo", "istockphoto.com/photo",
    "shutterstock.com/image", "gettyimages.com/photos",
    "stock-photo", "stock_photo", "stockphoto",
    "/generic/", "/placeholder/", "/default-",
    "wp-content/uploads/banner", "wp-content/uploads/logo",
    "wp-content/uploads/header", "wp-content/uploads/bg",
    "wp-content/uploads/background",
]


def _is_likely_stock_image(img_url, article_url=""):
    """
    Returns True if the image is likely a generic/stock image unrelated to the article.
    Checks both the image URL patterns and whether the source domain is a known offender.
    """
    if not img_url:
        return False
    il = img_url.lower()
    # Check stock URL fragments
    if any(frag in il for frag in _STOCK_URL_FRAGMENTS):
        return True
    # Check if article domain is a known stock-image offender
    if article_url:
        try:
            host = urllib.parse.urlparse(article_url).netloc.lower().lstrip("www.")
            if host in _STOCK_IMAGE_DOMAINS or any(host.endswith("." + d) for d in _STOCK_IMAGE_DOMAINS):
                return True
        except Exception:
            pass
    return False


def _resolve_article_image(urlToImage, rss_img, url, title):
    # 1. NewsAPI urlToImage — most reliable, but skip if from stock domain
    if urlToImage and _is_clean_image(urlToImage) and not _is_likely_stock_image(urlToImage, url):
        return urlToImage

    # 2. RSS media image — skip if stock
    if rss_img and _is_clean_image(rss_img) and not _is_likely_stock_image(rss_img, url):
        return rss_img

    # 3. Scrape OG image from the actual article page
    #    Skip scraping entirely for known stock-image domains
    if url and url.startswith("http") and not _is_likely_stock_image("", url):
        try:
            real_url = _resolve_real_url(url)
            scraped = fetch_article_image(real_url)
            if scraped and _is_clean_image(scraped) and not _is_likely_stock_image(scraped, url):
                return scraped
        except Exception:
            pass

    # 4. Smart topic fallback — uses article title for relevant placeholder
    return _make_fallback_image(title)


def fetch_article_image(url):
    if not url or not url.startswith("http"):
        return ""

    cached = THUMBNAIL_CACHE.get(url)
    if cached and (time.time() - cached["ts"]) < CACHE_EXPIRY_SECONDS:
        return cached["image"]

    def _store(img):
        THUMBNAIL_CACHE[url] = {"image": img, "ts": time.time()}
        return img

    real_url = _resolve_real_url(url)

    BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    try:
        ml = requests.get(
            "https://api.microlink.io",
            params={"url": real_url, "meta": "true"},
            timeout=6,
            headers={"User-Agent": "TruthLens/2.0"},
        )
        if ml.status_code == 200:
            data = ml.json()
            if data.get("status") == "success":
                img_obj = (data.get("data") or {}).get("image") or {}
                img_url = img_obj.get("url", "") if isinstance(img_obj, dict) else ""
                if img_url and _is_clean_image(img_url):
                    return _store(img_url)
    except Exception:
        pass

    try:
        resp = requests.get(
            real_url,
            headers={
                "User-Agent":      BROWSER_UA,
                "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT":             "1",
            },
            timeout=6,
            stream=True,
            allow_redirects=True,
        )
        if resp.status_code not in (200, 203):
            return _store("")
        ct = resp.headers.get("Content-Type", "")
        if ct and "html" not in ct:
            return _store("")

        raw = b""
        for chunk in resp.iter_content(chunk_size=8192):
            raw += chunk
            if len(raw) >= 300000:
                break

        html = raw.decode("utf-8", errors="ignore")
        result = _extract_og_image_from_html(html)

        if result and _is_clean_image(result):
            return _store(result)

    except Exception:
        pass

    return _store("")


_TOPIC_IMAGE_SEEDS = {
    "ukraine":      433, "russia":       434, "war":          435,
    "conflict":     436, "invasion":     437, "frontline":    438,
    "military":     439, "ceasefire":    440, "zelensky":     441,
    "zelenskyy":    442, "putin":        443, "nato":         444,
    "israel":       450, "gaza":         451, "hamas":        452,
    "palestine":    453, "trump":        460, "biden":        461,
    "kamala":       462, "election":     463, "president":    464,
    "congress":     465, "senate":       466, "democrat":     467,
    "republican":   468, "india":        470, "modi":         471,
    "delhi":        472, "mumbai":       473, "parliament":   474,
    "bjp":          475, "isro":         480, "chandrayaan":  481,
    "gaganyaan":    482, "moon":         483, "nasa":         484,
    "space":        485, "rocket":       486, "satellite":    487,
    "james webb":   488, "alphafold":    489, "deepmind":     490,
    "cricket":      500, "ipl":          501, "t20":          502,
    "rohit":        503, "kohli":        504, "world cup":    505,
    "artificial":   510, "intelligence": 511, "chatgpt":      512,
    "openai":       513, "google":       514, "apple":        515,
    "iphone":       516, "microsoft":    517, "musk":         518,
    "elon":         519, "tesla":        520, "twitter":      521,
    "facebook":     522, "meta":         523, "cyber":        524,
    "hack":         525, "economy":      530, "stock":        531,
    "market":       532, "bank":         533, "bitcoin":      534,
    "crypto":       535, "inflation":    536, "trade":        537,
    "sensex":       538, "nifty":        539, "covid":        540,
    "coronavirus":  541, "virus":        542, "vaccine":      543,
    "pandemic":     544, "health":       545, "hospital":     546,
    "doctor":       547, "mpox":         548, "climate":      550,
    "environment":  551, "flood":        552, "earthquake":   553,
    "global warming": 554, "fake":       560, "fact":         561,
    "misinformation": 562, "debunk":     563, "hoax":         564,
    "conspiracy":   565, "flat earth":   566, "moon landing": 567,
    "apollo":       568, "5g":           569, "chemtrail":    570,
    "illuminati":   571, "deep state":   572, "qanon":        573,
    "new world order": 574, "bill gates": 580, "zuckerberg":  581,
    "sam altman":   582, "soros":        583, "breaking":     590,
    "news":         591,
}

_NEUTRAL_SEEDS = [10, 20, 30, 40, 50, 60, 70, 80]


def _make_fallback_image(title):
    """
    Generate a topic-relevant placeholder using picsum.photos.
    Handles both English and Hindi title keywords so Hindi articles
    get relevant images instead of the generic 'news' bucket.
    """
    t = (title or "").lower()
    variation = abs(hash(title or "x")) % 10

    # ── Hindi keyword check (original Unicode, not lowercased) ───────────
    t_orig = (title or "")
    HINDI_MAP = [
        ("space",     ["इसरो", "चंद्रयान", "गगनयान", "अंतरिक्ष", "नासा",
                       "रॉकेट", "उपग्रह", "स्पेस", "अंतरिक्ष दिवस"]),
        ("sports",    ["क्रिकेट", "विश्व कप", "आईपीएल", "रोहित", "विराट",
                       "धोनी", "टी20", "खेल", "ओलंपिक", "टूर्नामेंट"]),
        ("health",    ["कोरोना", "कोविड", "वैक्सीन", "टीका", "स्वास्थ्य",
                       "अस्पताल", "वायरस", "महामारी", "दवा"]),
        ("factcheck", ["फेक", "झूठ", "अफवाह", "फर्जी", "फैक्ट",
                       "भ्रामक", "गलत", "माइक्रोचिप", "षड्यंत्र"]),
        ("tech",      ["तकनीक", "एआई", "आर्टिफिशियल", "मोबाइल", "इंटरनेट",
                       "5जी", "डिजिटल", "साइबर", "ऐप"]),
        ("finance",   ["अर्थव्यवस्था", "बजट", "शेयर", "बाजार", "रुपया",
                       "बैंक", "जीडीपी", "सेंसेक्स", "निफ्टी"]),
        ("war",       ["युद्ध", "रूस", "यूक्रेन", "हमला", "सेना",
                       "हमास", "इजरायल", "गाजा", "संघर्ष", "मिसाइल"]),
        ("politics",  ["चुनाव", "राजनीति", "नेता", "पार्टी", "ट्रम्प", "बाइडेन"]),
        ("india",     ["भारत", "मोदी", "संसद", "लोकसभा", "भाजपा", "कांग्रेस",
                       "दिल्ली", "मुंबई", "राष्ट्रीय", "मंत्री", "सरकार"]),
    ]
    for base, kw_list in HINDI_MAP:
        if any(kw in t_orig for kw in kw_list):
            return f"https://picsum.photos/seed/{base}{variation}/800/450"

    # ── English keyword matching ───────────────────────────────────────────
    if any(k in t for k in ["ukraine", "russia", "war", "conflict", "invasion",
                              "frontline", "zelensky", "putin", "nato", "military",
                              "missile", "troops", "combat"]):
        base = "war"
    elif any(k in t for k in ["israel", "gaza", "hamas", "palestine"]):
        base = "mideast"
    elif any(k in t for k in ["trump", "biden", "election", "president",
                                "congress", "senate", "democrat", "republican",
                                "white house", "capitol", "kamala"]):
        base = "politics"
    elif any(k in t for k in ["isro", "chandrayaan", "moon", "nasa", "space",
                                "rocket", "satellite", "gaganyaan", "lunar",
                                "orbit", "spacecraft", "astronaut", "space day",
                                "antriksh", "national space"]):
        base = "space"
    elif any(k in t for k in ["india", "modi", "delhi", "mumbai", "bjp",
                                "parliament", "lok sabha"]):
        base = "india"
    elif any(k in t for k in ["cricket", "ipl", "t20", "rohit", "kohli",
                                "world cup", "sports", "olympic", "fifa",
                                "tournament", "stadium"]):
        base = "sports"
    elif any(k in t for k in ["chatgpt", "openai", "artificial", " ai", "ai ",
                                "tech", "google", "apple", "microsoft", "musk",
                                "tesla", "5g", "cyber", "digital", "software"]):
        base = "tech"
    elif any(k in t for k in ["economy", "stock", "market", "bitcoin", "crypto",
                                "inflation", "finance", "bank", "gdp", "budget",
                                "sensex", "nifty", "rupee"]):
        base = "finance"
    elif any(k in t for k in ["covid", "coronavirus", "vaccine", "pandemic",
                                "virus", "health", "hospital", "doctor",
                                "disease", "medicine", "mpox"]):
        base = "health"
    elif any(k in t for k in ["climate", "environment", "flood", "earthquake",
                                "cyclone", "drought", "pollution"]):
        base = "climate"
    elif any(k in t for k in ["fake", "fact", "misinformation", "debunk",
                                "hoax", "conspiracy", "misleading"]):
        base = "factcheck"
    else:
        base = "news"

    return f"https://picsum.photos/seed/{base}{variation}/800/450"


def _rss_item_image(item):
    SKIP = ['logo', 'icon', 'sprite', 'favicon', 'avatar', 'placeholder',
            'blank', 'pixel', 'spacer', '1x1', 'tracking', 'beacon',
            'news.google.com', 'google.com/s2']

    def _clean(url):
        if not url:
            return ""
        url = url.strip()
        ul = url.lower()
        if ul.endswith(".svg") or ".svg?" in ul:
            return ""
        if any(s in ul for s in SKIP):
            return ""
        if not url.startswith(("http://", "https://", "//")):
            return ""
        return url

    for tag in ("media:content", "media:thumbnail"):
        el = item.find(tag)
        if el:
            img = _clean(el.get("url", ""))
            if img:
                return img

    enc = item.find("enclosure")
    if enc and "image" in enc.get("type", ""):
        img = _clean(enc.get("url", ""))
        if img:
            return img

    desc_el = item.find("description")
    if desc_el:
        desc_html = str(desc_el)
        m = re.search(r'<img[^>]+src=["\']([^"\']{10,})["\']', desc_html, re.I)
        if m:
            img = _clean(m.group(1))
            if img:
                return img

    return ""


def fetch_images_parallel(articles, url_key="link", image_key="image"):
    BLOCKED_DOMAINS = {"ft.com", "wsj.com"}

    def _domain_blocked(u):
        try:
            host = urllib.parse.urlparse(u).netloc.lower().lstrip("www.")
            return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)
        except Exception:
            return False

    import concurrent.futures as _cf

    missing = [i for i, a in enumerate(articles)
               if not a.get(image_key) or not _is_clean_image(a.get(image_key, ""))]
    scrapable = [i for i in missing
                 if not _domain_blocked(articles[i].get(url_key, ""))]

    if scrapable:
        max_workers = min(6, len(scrapable))
        with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(
                    _resolve_article_image,
                    articles[i].get("urlToImage", ""),
                    articles[i].get(image_key, ""),
                    articles[i].get(url_key, ""),
                    articles[i].get("title", "")
                ): i
                for i in scrapable
            }
            done, _ = _cf.wait(futures, timeout=12)
            for fut in done:
                i = futures[fut]
                try:
                    img = fut.result()
                    if img and _is_clean_image(img):
                        articles[i][image_key] = img
                except Exception:
                    pass

    for article in articles:
        img = article.get(image_key, "")
        if img and not _is_clean_image(img):
            article[image_key] = _make_fallback_image(
                article.get("title", "") or article.get("desc", "")
            )
        elif not img:
            article[image_key] = _make_fallback_image(
                article.get("title", "") or article.get("desc", "")
            )


def is_hindi(text):
    return sum(1 for c in text if '\u0900' <= c <= '\u097F') > 5

def has_debunk_signal(text):
    t = text.lower()
    return any(sig in t for sig in DEBUNK_SIGNALS)

def time_ago(published_at):
    if not published_at:
        return ""
    try:
        pub = datetime.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S")
        diff = datetime.utcnow() - pub
        hours = int(diff.total_seconds() // 3600)
        if hours < 1:
            return f"{int(diff.total_seconds()//60)} min ago"
        elif hours < 24:
            return f"{hours}h ago"
        else:
            d = hours // 24
            return f"{d} day{'s' if d != 1 else ''} ago"
    except:
        return published_at[:10]


# ─── Wikipedia ────────────────────────────────────────────────────────────────

def fetch_wiki_image(page_title):
    SKIP = ["flag","icon","logo","symbol","edit","question","OOjs","Portal",
            "nuvola","Ambox","Wiki","commons/thumb/0","commons/thumb/f"]
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action":      "query",
                "titles":      page_title,
                "prop":        "pageimages",
                "pithumbsize": 800,
                "piprop":      "original|thumbnail|name",
                "format":      "json",
                "formatversion": "2"
            },
            timeout=7, headers={"User-Agent": "TruthLens/2.0 (educational)"})
        if r.status_code == 200:
            pages = r.json().get("query", {}).get("pages", [])
            for page in pages:
                orig = page.get("original", {})
                src = orig.get("source", "")
                if not src:
                    src = page.get("thumbnail", {}).get("source", "")
                if src and not any(s in src for s in SKIP):
                    if "/thumb/" in src:
                        src = re.sub(r'/\d+px-', '/800px-', src)
                    return src
    except:
        pass
    try:
        encoded = urllib.parse.quote(page_title.replace(" ", "_"))
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}",
            timeout=6, headers={"User-Agent": "TruthLens/2.0"})
        if r.status_code == 200:
            data = r.json()
            src = (data.get("originalimage") or {}).get("source", "")
            if not src:
                src = (data.get("thumbnail") or {}).get("source", "")
            if src and not any(s in src for s in SKIP):
                if "/thumb/" in src:
                    src = re.sub(r'/\d+px-', '/800px-', src)
                return src
    except:
        pass
    return ""


def fetch_wikipedia_context(claim_keywords, hindi=False, original_text="",
                            kb_match_real=None, kb_match_fake=None):
    """
    UPDATED: Guaranteed to always return a Wikipedia result — never empty {}.

    Candidate generation priority (strict):
    1. detect_event_type() on original text         → event page (highest)
    2. detect_event_type() on Hindi→English text    → event page (Hindi input)
    3. resolve_wiki_topic() on original text        → best phrase match
    4. resolve_wiki_topic() on Hindi→English text   → translated match
    5. KB fake topic → misinformation wiki page
    6. First-mentioned known entity                 → person/org page
    7. Event phrases                                → topic page
    8. Hindi Wikipedia for same topics              → Hindi version
    9. Hard fallbacks: "Misinformation", "India", "News media", "Current events"
       (guaranteed to exist on Wikipedia — never fails)

    CRITICAL: Uses smart_cap() NOT title() — title() corrupts apostrophes and
    mixed-case names like "ICC Men's T20 World Cup" → "Icc Men'S T20 World Cup".
    """
    candidates = []   # list of (wiki_title_str, prefer_hindi_bool)
    seen_c     = set()

    def _add(title, pref_hi=False):
        t = (title or "").strip()
        tl = t.lower()
        if t and len(t) > 1 and tl not in seen_c:
            seen_c.add(tl)
            candidates.append((t, pref_hi))

    def _smart_cap(s):
        """Capitalize only the first character — preserve all other casing.
        Avoids corrupting titles like "ICC Men's T20 World Cup" or "COVID-19".
        """
        if not s:
            return s
        return s[0].upper() + s[1:]

    # ── 1. Event detection on ORIGINAL text — always highest priority ─────
    _, event_wiki_orig = detect_event_type(original_text)
    _add(event_wiki_orig, False)

    # ── 2. Hindi → English translation + event detection ─────────────────
    en_kw      = []
    en_text    = ""
    if hindi or is_hindi(original_text):
        en_kw   = _hindi_to_english_keywords(original_text)
        en_text = " ".join(en_kw)
        if en_text:
            # Run detect_event_type on the translated text too
            _, event_wiki_en = detect_event_type(en_text)
            # Only add if different from what we already have
            _add(event_wiki_en, False)
            # IMPORTANT: if original text already gave a good event topic,
            # the translated version should give the SAME result —
            # that's the fix for "same news Hindi/English → same Wikipedia"

    # ── 3. resolve_wiki_topic on original text ────────────────────────────
    resolved_orig = resolve_wiki_topic(claim_keywords, original_text)
    _add(resolved_orig, False)   # always English wiki — more reliable

    # ── 4. resolve_wiki_topic on translated English text ──────────────────
    if en_text:
        resolved_en = resolve_wiki_topic(en_kw, en_text)
        _add(resolved_en, False)

    # ── 5. KB fake match → misinformation Wikipedia page ──────────────────
    if kb_match_fake and kb_match_fake[0]:
        kb_desc     = kb_match_fake[0]
        kb_resolved = resolve_wiki_topic([], kb_desc)
        _add(kb_resolved, False)
        topic_part = kb_desc.split("—")[0].strip().split("/")[0].strip()
        _add(topic_part, False)

    # ── 6. First-mentioned known entity ───────────────────────────────────
    work_text = en_text if en_text else original_text
    entities  = extract_known_entities(work_text)
    if entities:
        tl_ref = work_text.lower()
        def _pos(e):
            p = tl_ref.find(e.lower())
            return p if p >= 0 else len(work_text)
        for e in sorted(entities, key=_pos)[:3]:
            _add(e, False)

    # ── 7. Event phrases ──────────────────────────────────────────────────
    for ep in extract_event_phrases(work_text)[:3]:
        _add(ep, False)

    # ── 8. Hindi Wikipedia — only try for genuinely Hindi-named articles ──
    # Don't blindly add prefer_hindi=True for English Wikipedia titles
    # (Hindi Wikipedia titles are different from English ones)
    if hindi and candidates:
        top_title = candidates[0][0]
        # Only try Hindi wiki if the title has a known Hindi equivalent
        _add(top_title, True)

    # ── 9. Hard fallbacks — these ALWAYS exist on Wikipedia ───────────────
    hard_fallbacks = []
    if kb_match_fake:
        hard_fallbacks += ["Misinformation", "Fake news", "Conspiracy theory"]
    if hindi or is_hindi(original_text):
        hard_fallbacks += ["India", "Indian media", "Hindi"]
    hard_fallbacks += ["Current events", "News media", "Journalism"]
    for fb in hard_fallbacks:
        _add(fb, False)

    # ── Try each candidate against Wikipedia REST API ─────────────────────
    seen_titles = set()
    for title, prefer_hindi in candidates:
        tl = title.lower().strip()
        if tl in seen_titles or len(tl) < 2:
            continue
        seen_titles.add(tl)

        # For prefer_hindi: try Hindi API first, then English
        apis_to_try = ([WIKIPEDIA_HI_API, WIKIPEDIA_API] if prefer_hindi
                       else [WIKIPEDIA_API])

        for api in apis_to_try:
            try:
                is_hi_api = "hi.wikipedia" in api
                # CRITICAL FIX: use _smart_cap() NOT .title()
                # .title() corrupts "ICC Men's T20" → "Icc Men'S T20" (broken)
                # _smart_cap() only capitalizes first letter (correct)
                if is_hi_api:
                    search_title = title   # Hindi titles: use as-is
                else:
                    search_title = _smart_cap(title)  # English: only first letter up

                r = requests.get(
                    api + urllib.parse.quote(search_title),
                    timeout=6, headers={"User-Agent": "TruthLens/2.0"})
                if r.status_code != 200:
                    continue
                data = r.json()
                if data.get("type") != "standard" or not data.get("extract"):
                    continue
                page_title_actual = data.get("title", title)
                image = ""
                orig_img = data.get("originalimage", {})
                if orig_img.get("source"):
                    image = orig_img["source"]
                if not image:
                    thumb = data.get("thumbnail", {}).get("source", "")
                    if thumb:
                        image = re.sub(r'/\d+px-', '/800px-', thumb)
                if not image:
                    image = fetch_wiki_image(page_title_actual)
                extract = data.get("extract", "")
                return {
                    "title":       page_title_actual,
                    "extract":     (extract[:500] + "\u2026" if len(extract) > 500 else extract),
                    "url":         data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    "image":       image,
                    "description": data.get("description", ""),
                }
            except Exception:
                continue

    # ── Absolute last resort — should never reach here ────────────────────
    return {}


# ─── NewsAPI with Hindi prioritization ────────────────────────────────────────

def _build_news_queries(claim_text, prediction, for_hindi=False):
    """
    Build a prioritised list of NewsAPI query strings.
    Q1 is ALWAYS the exact event (from detect_event_type) — most specific.
    Subsequent queries broaden gradually.
    Hindi input: translates first, then builds English queries.
    """
    # For Hindi: translate first so we get proper English queries
    work_text = claim_text
    if is_hindi(claim_text) or for_hindi:
        hi_kw = _hindi_to_english_keywords(claim_text)
        if hi_kw:
            work_text = " ".join(hi_kw)

    year          = extract_year(work_text) or extract_year(claim_text)
    _, event_wiki = detect_event_type(work_text) or detect_event_type(claim_text)
    kw            = extract_newsapi_keywords(work_text)

    def _dedup_q(*parts):
        seen_w, out = set(), []
        for part in (parts or []):
            for w in (part or "").split():
                if w.lower() not in seen_w:
                    seen_w.add(w.lower()); out.append(w)
        return " ".join(out).strip()

    # Build the event-wiki query — strip only true filler, keep key terms
    q_event = ""
    if event_wiki:
        skip_ew = {"the", "a", "an", "of", "by", "in", "on", "at", "and", "or",
                   "conspiracy", "misinformation", "theories", "theory", "denial"}
        ew_words = [w for w in event_wiki.split() if w.lower() not in skip_ew]
        q_event = " ".join(ew_words[:6])   # keep up to 6 words for specificity

    q_main  = _dedup_q(*kw[:4])
    q_short = _dedup_q(*kw[:3])
    q_pair  = _dedup_q(*kw[:2])

    if prediction == "FAKE NEWS":
        queries = [
            q_event + " fact check" if q_event else q_main + " fact check",
            q_event + " debunked"   if q_event else q_main + " debunked",
            q_event or q_main,
            q_main,
            q_short + " misinformation",
            q_pair,
        ]
    else:
        queries = [
            q_event or q_main,   # ALWAYS lead with exact event
            q_main,
            q_short,
            q_pair,
            (q_event or q_main) + " news",
        ]

    if for_hindi:
        queries = [q + " india" if q and "india" not in q.lower() else q for q in queries]

    # Deduplicate while preserving order
    seen_q, clean = set(), []
    for q in queries:
        q = (q or "").strip()
        ql = q.lower()
        if q and len(q) > 3 and ql not in seen_q:
            seen_q.add(ql); clean.append(q)
    return clean


def fetch_related_news(claim_text, prediction, all_keywords):
    hindi = is_hindi(claim_text)
    # _build_news_queries handles Hindi translation internally now
    queries = _build_news_queries(claim_text, prediction, for_hindi=False)

    articles_out = []
    seen_urls    = set()

    def _make_article(a):
        url         = a.get("url", "")
        title       = a.get("title") or ""
        if not url or url in seen_urls or title in ("[Removed]", "") or not title:
            return None
        seen_urls.add(url)
        desc        = a.get("description") or ""
        image       = _resolve_article_image(a.get("urlToImage", ""), "", url, title)
        source_name = (a.get("source") or {}).get("name") or ""
        published   = a.get("publishedAt") or ""
        return {
            "title":       title,
            "description": (desc[:200] + "…") if len(desc) > 200 else desc,
            "link":        url,
            "image":       image,
            "urlToImage":  a.get("urlToImage", ""),
            "source":      source_name,
            "favicon":     get_source_favicon(source_name),
            "initials":    get_source_initials(source_name),
            "published":   time_ago(published),
            "is_debunk":   has_debunk_signal(title + " " + desc),
        }

    # ── PHASE 1 (Hindi): Indian sources with translated queries ───────────
    if hindi:
        # Use for_hindi=True so _build_news_queries translates + appends "india"
        hi_queries = _build_news_queries(claim_text, prediction, for_hindi=True)
        for q in hi_queries[:4]:
            if len(articles_out) >= 3:
                break
            for lang in ["hi", "en"]:
                if len(articles_out) >= 3:
                    break
                try:
                    data = requests.get(
                        "https://newsapi.org/v2/everything",
                        params={"q": q, "apiKey": NEWS_API_KEY, "pageSize": 10,
                                "sortBy": "relevancy", "language": lang,
                                "domains": HINDI_NEWS_DOMAINS},
                        timeout=8).json()
                    if data.get("status") != "ok":
                        continue
                    for a in data.get("articles", []):
                        art = _make_article(a)
                        if art:
                            articles_out.append(art)
                        if len(articles_out) >= 3:
                            break
                except Exception:
                    continue

        # ── Hindi Google News RSS (actual Hindi results) ───────────────────
        if len(articles_out) < 3:
            hi_raw = re.findall(r'[\u0900-\u097F\s]{3,}', claim_text)
            hi_text = " ".join(hi_raw).strip()[:80] if hi_raw else ""
            for rss_q in ([hi_text] if hi_text else []) + hi_queries[:2]:
                if len(articles_out) >= 3 or not rss_q:
                    break
                try:
                    q_enc  = urllib.parse.quote(rss_q)
                    r      = requests.get(
                        f"https://news.google.com/rss/search?q={q_enc}&hl=hi-IN&gl=IN&ceid=IN:hi",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                    soup   = BeautifulSoup(r.content, "xml")
                    for item in soup.find_all("item")[:10]:
                        if len(articles_out) >= 3:
                            break
                        title_el  = item.find("title")
                        link_el   = item.find("link")
                        source_el = item.find("source")
                        if not title_el:
                            continue
                        title   = title_el.get_text(strip=True)
                        raw_url = link_el.get_text(strip=True) if link_el else ""
                        if not raw_url:
                            continue
                        real_url = _resolve_real_url(raw_url)
                        use_url  = real_url if (real_url and "news.google.com" not in real_url) else raw_url
                        if use_url in seen_urls:
                            continue
                        seen_urls.add(use_url)
                        rss_img = _rss_item_image(item)
                        image   = _resolve_article_image("", rss_img,
                                      real_url if "news.google.com" not in real_url else "", title)
                        src_name = source_el.get_text(strip=True) if source_el else "Google News"
                        articles_out.append({
                            "title": title, "description": "",
                            "link": use_url, "image": image, "urlToImage": "",
                            "source": src_name,
                            "favicon": get_source_favicon(src_name),
                            "initials": get_source_initials(src_name),
                            "published": "", "is_debunk": has_debunk_signal(title.lower()),
                        })
                except Exception:
                    pass

    # ── PHASE 2: English NewsAPI — iterate all queries until 6 articles ───
    for q in queries:
        if len(articles_out) >= 6:
            break
        try:
            data = requests.get(
                "https://newsapi.org/v2/everything",
                params={"q": q, "apiKey": NEWS_API_KEY, "pageSize": 15,
                        "sortBy": "relevancy", "language": "en"},
                timeout=8).json()
            if data.get("status") != "ok":
                continue
            for a in data.get("articles", []):
                art = _make_article(a)
                if art:
                    articles_out.append(art)
                if len(articles_out) >= 6:
                    break
        except Exception:
            continue

    # ── PHASE 3: Google News RSS fallback ────────────────────────────────
    if len(articles_out) < 3:
        lang_param = "hl=hi-IN&gl=IN&ceid=IN:hi" if hindi else "hl=en-IN&gl=IN&ceid=IN:en"
        for rss_q in queries[:3]:
            if len(articles_out) >= 6:
                break
            try:
                q_enc = urllib.parse.quote(rss_q)
                r     = requests.get(
                    f"https://news.google.com/rss/search?q={q_enc}&{lang_param}",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                soup  = BeautifulSoup(r.content, "xml")
                for item in soup.find_all("item")[:12]:
                    if len(articles_out) >= 6:
                        break
                    title_el  = item.find("title")
                    link_el   = item.find("link")
                    source_el = item.find("source")
                    if not title_el:
                        continue
                    title   = title_el.get_text(strip=True)
                    raw_url = link_el.get_text(strip=True) if link_el else ""
                    if not raw_url:
                        continue
                    real_url = _resolve_real_url(raw_url)
                    use_url  = real_url if (real_url and "news.google.com" not in real_url) else raw_url
                    if use_url in seen_urls:
                        continue
                    seen_urls.add(use_url)
                    rss_img  = _rss_item_image(item)
                    image    = _resolve_article_image("", rss_img,
                                   real_url if "news.google.com" not in real_url else "", title)
                    src_name = source_el.get_text(strip=True) if source_el else "Google News"
                    articles_out.append({
                        "title": title, "description": "",
                        "link": use_url, "image": image, "urlToImage": "",
                        "source": src_name,
                        "favicon": get_source_favicon(src_name),
                        "initials": get_source_initials(src_name),
                        "published": "", "is_debunk": has_debunk_signal(title.lower()),
                    })
            except Exception:
                pass

    fetch_images_parallel(articles_out, url_key="link", image_key="image")
    return articles_out[:6]


def fetch_more_articles(claim_text, prediction, all_keywords, exclude_urls=None):
    """
    MORE SUPPORTING COVERAGE section.
    Deliberately uses DIFFERENT query angles from fetch_related_news to avoid
    showing the same articles twice — focuses on secondary entities, event wiki
    title, and fact-check/debunk angles for fake news.
    """
    if exclude_urls is None:
        exclude_urls = set()

    hindi         = is_hindi(claim_text)
    year          = extract_year(claim_text)
    _, event_wiki = detect_event_type(claim_text)
    kw            = extract_newsapi_keywords(claim_text)

    # Build queries using DIFFERENT emphasis than fetch_related_news:
    # - Swap person order (secondary first)
    # - Use event_wiki title directly
    # - Add different angles: analysis, explained, latest
    _people_set    = {x.lower() for x in KNOWN_ENTITIES[:22]}
    entities       = extract_known_entities(claim_text)
    persons        = [e for e in entities if e.lower() in _people_set]
    orgs           = [e for e in entities if e.lower() not in _people_set]
    second_person  = persons[1] if len(persons) > 1 else (persons[0] if persons else "")
    primary_org    = orgs[0] if orgs else ""

    q_wiki  = ""
    if event_wiki:
        skip_ew = {"the","a","an","of","by","in","on","at","and","or","conspiracy",
                   "misinformation","theories","theory","denial"}
        ew_w = [w for w in event_wiki.split() if w.lower() not in skip_ew]
        q_wiki = " ".join(ew_w[:5])

    q_main  = " ".join(kw[:3])
    q_short = " ".join(kw[:2])

    if prediction == "FAKE NEWS":
        queries = [
            q_wiki + " false" if q_wiki else q_main + " false",
            q_main + " debunked",
            second_person + " " + (year or q_short) if second_person else q_main,
            q_wiki or q_short,
            q_main + " fact check",
            q_short + " hoax" if not q_wiki else q_wiki + " debunked",
        ]
    else:
        queries = [
            q_wiki or q_main,
            second_person + " " + (year or "") if second_person else q_short,
            primary_org + " " + (year or "") if primary_org else q_main,
            q_main + " explained",
            q_main + " latest",
            q_short + " " + (year or "news"),
        ]

    if hindi:
        hi_kw   = _hindi_to_english_keywords(claim_text)
        hi_main = " ".join(hi_kw[:3]) if hi_kw else q_main
        queries = [hi_main + " india"] + queries

    # Deduplicate queries
    seen_q, clean_q = set(), []
    for q in queries:
        q = q.strip()
        ql = q.lower()
        if q and len(q) > 3 and ql not in seen_q:
            seen_q.add(ql); clean_q.append(q)
    queries = clean_q

    articles_out = []
    seen_urls    = set(exclude_urls)

    def _make_article(a):
        url         = a.get("url", "")
        title       = a.get("title") or ""
        if not url or url in seen_urls or title in ("[Removed]", "") or not title:
            return None
        seen_urls.add(url)
        desc        = a.get("description") or ""
        image       = _resolve_article_image(a.get("urlToImage", ""), "", url, title)
        source_name = (a.get("source") or {}).get("name") or ""
        published   = a.get("publishedAt") or ""
        return {
            "title":     title,
            "desc":      (desc[:160] + "…") if len(desc) > 160 else desc,
            "link":      url,
            "image":     image,
            "urlToImage": a.get("urlToImage", ""),
            "source":    source_name,
            "favicon":   get_source_favicon(source_name),
            "initials":  get_source_initials(source_name),
            "published": time_ago(published),
            "is_debunk": has_debunk_signal(title + " " + desc),
        }

    # ── PHASE 1 (Hindi): Indian sources in both languages ─────────────────
    if hindi:
        hi_kw      = _hindi_to_english_keywords(claim_text)
        hi_queries = _build_news_queries(" ".join(hi_kw) if hi_kw else claim_text,
                                         prediction, for_hindi=True)
        for q in hi_queries[:3]:
            if len(articles_out) >= 2:
                break
            for lang in ["hi", "en"]:
                if len(articles_out) >= 2:
                    break
                try:
                    data = requests.get(
                        "https://newsapi.org/v2/everything",
                        params={"q": q, "apiKey": NEWS_API_KEY, "pageSize": 8,
                                "sortBy": "relevancy", "language": lang,
                                "domains": HINDI_NEWS_DOMAINS},
                        timeout=7).json()
                    if data.get("status") != "ok":
                        continue
                    for a in data.get("articles", []):
                        art = _make_article(a)
                        if art:
                            articles_out.append(art)
                        if len(articles_out) >= 2:
                            break
                except Exception:
                    continue

    # ── PHASE 2: English NewsAPI ──────────────────────────────────────────
    for q in queries:
        if len(articles_out) >= 5:
            break
        try:
            data = requests.get(
                "https://newsapi.org/v2/everything",
                params={"q": q, "apiKey": NEWS_API_KEY, "pageSize": 12,
                        "sortBy": "relevancy", "language": "en"},
                timeout=7).json()
            if data.get("status") != "ok":
                continue
            for a in data.get("articles", []):
                art = _make_article(a)
                if art:
                    articles_out.append(art)
                if len(articles_out) >= 5:
                    break
        except Exception:
            continue

    # ── PHASE 3: Google News RSS fallback ────────────────────────────────
    if len(articles_out) < 3:
        lang_p = "hl=hi-IN&gl=IN&ceid=IN:hi" if hindi else "hl=en-IN&gl=IN&ceid=IN:en"
        for rss_q in queries[:3]:
            if len(articles_out) >= 5:
                break
            try:
                q_enc = urllib.parse.quote(rss_q)
                r     = requests.get(
                    f"https://news.google.com/rss/search?q={q_enc}&{lang_p}",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                soup  = BeautifulSoup(r.content, "xml")
                for item in soup.find_all("item")[:10]:
                    if len(articles_out) >= 5:
                        break
                    title_el  = item.find("title")
                    link_el   = item.find("link")
                    source_el = item.find("source")
                    pub_el    = item.find("pubDate")
                    if not title_el:
                        continue
                    title   = title_el.get_text(strip=True)
                    raw_url = link_el.get_text(strip=True) if link_el else ""
                    if not raw_url:
                        continue
                    real_url = _resolve_real_url(raw_url)
                    use_url  = real_url if (real_url and "news.google.com" not in real_url) else raw_url
                    if use_url in seen_urls:
                        continue
                    seen_urls.add(use_url)
                    t_ago   = ""
                    pub     = pub_el.get_text(strip=True) if pub_el else ""
                    if pub:
                        try:
                            dt   = parsedate_to_datetime(pub)
                            diff = datetime.utcnow() - dt.replace(tzinfo=None)
                            h    = int(diff.total_seconds() // 3600)
                            t_ago = f"{h}h" if h < 24 else f"{h//24}d"
                        except Exception:
                            pass
                    rss_img  = _rss_item_image(item)
                    image    = _resolve_article_image("", rss_img,
                                   real_url if "news.google.com" not in real_url else "", title)
                    src_name = source_el.get_text(strip=True) if source_el else "Google News"
                    articles_out.append({
                        "title": title, "desc": "",
                        "link": use_url, "image": image, "urlToImage": "",
                        "source": src_name,
                        "favicon": get_source_favicon(src_name),
                        "initials": get_source_initials(src_name),
                        "published": t_ago,
                        "is_debunk": has_debunk_signal(title.lower()),
                    })
            except Exception:
                pass

    fetch_images_parallel(articles_out, url_key="link", image_key="image")
    return articles_out[:5]


# ─── Live Twitter via Nitter ──────────────────────────────────────────────────

def fetch_nitter_discussion(claim_text, keywords, prediction):
    """
    Fetches 5 tweets using 5+ focused, news-specific queries from generate_twitter_query().
    Each query targets a different angle of the story — person, event, year, pair, fact-check.
    Falls back to clickable Twitter search links using the same diverse queries.
    """
    # ── Generate 5-7 focused, diverse queries ────────────────────────────
    twitter_queries = generate_twitter_query(claim_text, keywords, prediction)

    tweets   = []
    seen_txt = set()

    # ── Try Nitter RSS for each query ─────────────────────────────────────
    for query in twitter_queries:
        if len(tweets) >= 5:
            break
        encoded_q = urllib.parse.quote(query)
        fetched_this_query = False
        for instance in NITTER_INSTANCES:
            if len(tweets) >= 5:
                break
            try:
                rss_url = f"{instance}/search/rss?q={encoded_q}&f=tweets"
                r = requests.get(
                    rss_url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; TruthLens/2.0)"},
                    timeout=5,
                )
                if r.status_code != 200:
                    continue
                soup  = BeautifulSoup(r.content, "xml")
                items = soup.find_all("item")
                if not items:
                    continue
                for item in items[:20]:
                    if len(tweets) >= 5:
                        break
                    title_el   = item.find("title")
                    link_el    = item.find("link")
                    desc_el    = item.find("description")
                    pub_el     = item.find("pubDate")
                    creator_el = item.find("dc:creator") or item.find("creator")
                    if not title_el:
                        continue
                    url = link_el.get_text(strip=True) if link_el else ""
                    tweet_text = ""
                    if desc_el:
                        raw_desc   = desc_el.get_text(strip=True)
                        tweet_text = re.sub(r'<[^>]+>', '', raw_desc)[:280]
                    if not tweet_text:
                        tweet_text = title_el.get_text(strip=True)
                    tweet_key = tweet_text[:80].lower()
                    if tweet_key in seen_txt:
                        continue
                    seen_txt.add(tweet_key)
                    username = creator_el.get_text(strip=True) if creator_el else "unknown"
                    if not username.startswith("@"):
                        username = "@" + username.replace(" ", "_")
                    pub   = pub_el.get_text(strip=True) if pub_el else ""
                    t_ago = ""
                    if pub:
                        try:
                            dt   = parsedate_to_datetime(pub)
                            diff = datetime.utcnow() - dt.replace(tzinfo=None)
                            h    = int(diff.total_seconds() // 3600)
                            t_ago = (f"{int(diff.total_seconds()//60)}m"
                                     if h < 1 else f"{h}h" if h < 24 else f"{h//24}d")
                        except Exception:
                            pass
                    combined     = tweet_text.lower()
                    is_debunk    = has_debunk_signal(combined)
                    is_spreading = any(w in combined for w in
                                       ["viral", "spreading", "shares", "retweet",
                                        "trending", "millions", "shared"])
                    sentiment = ("debunk" if is_debunk else
                                 "spreading" if is_spreading else "neutral")
                    twitter_url = re.sub(
                        r'https?://(nitter\.[^/]+)', 'https://twitter.com', url
                    ) if url else ""
                    tweets.append({
                        "text":       tweet_text,
                        "username":   username,
                        "time_ago":   t_ago,
                        "url":        twitter_url or url,
                        "nitter_url": url,
                        "sentiment":  sentiment,
                        "source":     "nitter",
                    })
                    fetched_this_query = True
                if fetched_this_query:
                    break   # Got results from this instance → move to next query
            except Exception:
                continue

    # ── RSSHub fallback ───────────────────────────────────────────────────
    if len(tweets) < 3:
        for query in twitter_queries[:3]:
            if len(tweets) >= 5:
                break
            try:
                q_enc = urllib.parse.quote(query)
                r = requests.get(
                    f"https://rsshub.app/twitter/search/{q_enc}",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=5,
                )
                if r.status_code == 200:
                    soup = BeautifulSoup(r.content, "xml")
                    for item in soup.find_all("item")[:10]:
                        if len(tweets) >= 5:
                            break
                        title_el   = item.find("title")
                        link_el    = item.find("link")
                        creator_el = item.find("dc:creator") or item.find("author")
                        pub_el     = item.find("pubDate")
                        if not title_el:
                            continue
                        tweet_text = title_el.get_text(strip=True)[:280]
                        tweet_key  = tweet_text[:80].lower()
                        if tweet_key in seen_txt:
                            continue
                        seen_txt.add(tweet_key)
                        username = creator_el.get_text(strip=True) if creator_el else "@user"
                        if not username.startswith("@"):
                            username = "@" + re.sub(r'[^a-z0-9_]', '', username.lower())[:15]
                        url   = link_el.get_text(strip=True) if link_el else ""
                        pub   = pub_el.get_text(strip=True) if pub_el else ""
                        t_ago = ""
                        if pub:
                            try:
                                dt   = parsedate_to_datetime(pub)
                                diff = datetime.utcnow() - dt.replace(tzinfo=None)
                                h    = int(diff.total_seconds() // 3600)
                                t_ago = f"{h}h" if h < 24 else f"{h//24}d"
                            except Exception:
                                pass
                        is_debunk = has_debunk_signal(tweet_text.lower())
                        tweets.append({
                            "text":       tweet_text,
                            "username":   username,
                            "time_ago":   t_ago,
                            "url":        url,
                            "nitter_url": url,
                            "sentiment":  "debunk" if is_debunk else "neutral",
                            "source":     "nitter",
                        })
            except Exception:
                pass

    # ── Search-link fallback — one link per query, up to 5 total ─────────
    # Always fill to 5 using clickable Twitter/X search links
    seen_search = set(t.get("search_query", "") for t in tweets)
    for query_str in twitter_queries:
        if len(tweets) >= 5:
            break
        if query_str in seen_search:
            continue
        seen_search.add(query_str)
        twitter_search_url = (
            "https://twitter.com/search?q="
            + urllib.parse.quote(query_str)
            + "&src=typed_query&f=live"
        )
        tweets.append({
            "text":           f"Search Twitter/X for: {query_str}",
            "username":       "@TwitterSearch",
            "time_ago":       "live",
            "url":            twitter_search_url,
            "nitter_url":     twitter_search_url,
            "sentiment":      "neutral",
            "badge":          "🔍 SEARCH",
            "source":         "search",
            "search_query":   query_str,
            "is_search_link": True,
        })

    if prediction == "FAKE NEWS":
        tweets.sort(key=lambda t: 0 if t["sentiment"] == "debunk" else 1)

    return tweets[:5]




# ─── Live Reddit Posts ────────────────────────────────────────────────────────

def fetch_reddit_posts(claim_text, all_keywords, prediction, kb_match_fake=None):
    kw = [k for k in all_keywords if all(ord(c) < 128 for c in k)][:5]
    if not kw:
        kw = extract_newsapi_keywords(claim_text)
    query_parts = kw[:4]

    if prediction == "FAKE NEWS":
        base_q = " ".join(query_parts[:3])
        queries = [
            base_q,
            " ".join(query_parts[:2]),
            kw[0] if kw else claim_text[:40],
        ]
        if kb_match_fake and kb_match_fake[0]:
            kb_topic = kb_match_fake[0].split("—")[0].strip()[:60]
            if kb_topic:
                queries.insert(0, kb_topic)
        subreddits = ["r/worldnews", "r/Snopes", "r/skeptic",
                      "r/factcheck", "r/politics", "r/news", "r/india"]
    else:
        queries = [" ".join(query_parts), " ".join(query_parts[:3])]
        subreddits = ["r/worldnews", "r/news", "r/india",
                      "r/technology", "r/science", "r/cricket"]

    posts = []
    seen  = set()
    headers = {"User-Agent": "TruthLens/2.0 (fake-news-detector)", "Accept": "application/json"}

    for q in queries[:2]:
        if len(posts) >= 5:
            break
        try:
            search_url = f"https://www.reddit.com/search.json?q={urllib.parse.quote(q)}&sort=relevance&limit=15&t=year"
            r = requests.get(search_url, headers=headers, timeout=8)
            if r.status_code == 200:
                data = r.json()
                for child in data.get("data", {}).get("children", []):
                    if len(posts) >= 5:
                        break
                    p = child.get("data", {})
                    title = p.get("title", "")
                    permalink = p.get("permalink", "")
                    url = "https://www.reddit.com" + permalink if permalink else p.get("url", "")
                    subreddit = p.get("subreddit_name_prefixed", "r/news")
                    score = p.get("score", 0)
                    num_comments = p.get("num_comments", 0)
                    created_utc = p.get("created_utc", 0)
                    selftext = p.get("selftext", "")[:200]
                    is_debunk = has_debunk_signal((title + " " + selftext).lower())
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    t_ago = ""
                    if created_utc:
                        diff = datetime.utcnow() - datetime.utcfromtimestamp(created_utc)
                        h = int(diff.total_seconds() // 3600)
                        t_ago = (f"{int(diff.total_seconds()//60)}m" if h < 1
                                 else f"{h}h" if h < 24 else f"{h//24}d")
                    posts.append({
                        "title":        title,
                        "text":         selftext,
                        "subreddit":    subreddit,
                        "score":        score,
                        "num_comments": num_comments,
                        "url":          url,
                        "time_ago":     t_ago,
                        "is_debunk":    is_debunk,
                        "source":       "reddit",
                    })
        except Exception:
            pass

    if len(posts) < 3:
        for sr in subreddits[:3]:
            if len(posts) >= 5:
                break
            try:
                q = " ".join(query_parts[:3])
                url = f"https://www.reddit.com/{sr}/search.json?q={urllib.parse.quote(q)}&restrict_sr=1&sort=relevance&limit=8"
                r = requests.get(url, headers=headers, timeout=6)
                if r.status_code == 200:
                    data = r.json()
                    for child in data.get("data", {}).get("children", []):
                        if len(posts) >= 5:
                            break
                        p = child.get("data", {})
                        title = p.get("title", "")
                        permalink = p.get("permalink", "")
                        post_url = "https://www.reddit.com" + permalink if permalink else ""
                        subreddit = p.get("subreddit_name_prefixed", sr)
                        score = p.get("score", 0)
                        num_comments = p.get("num_comments", 0)
                        created_utc = p.get("created_utc", 0)
                        is_debunk = has_debunk_signal(title.lower())
                        if not title or title in seen:
                            continue
                        seen.add(title)
                        t_ago = ""
                        if created_utc:
                            diff = datetime.utcnow() - datetime.utcfromtimestamp(created_utc)
                            h = int(diff.total_seconds() // 3600)
                            t_ago = (f"{int(diff.total_seconds()//60)}m" if h < 1
                                     else f"{h}h" if h < 24 else f"{h//24}d")
                        posts.append({
                            "title":        title,
                            "text":         "",
                            "subreddit":    subreddit,
                            "score":        score,
                            "num_comments": num_comments,
                            "url":          post_url,
                            "time_ago":     t_ago,
                            "is_debunk":    is_debunk,
                            "source":       "reddit",
                        })
            except Exception:
                continue

    if prediction == "FAKE NEWS":
        posts.sort(key=lambda p: 0 if p["is_debunk"] else 1)

    return posts[:5]


# ─── Google Fact Check ────────────────────────────────────────────────────────

def fetch_google_factchecks(query):
    try:
        r = requests.get(
            "https://factchecktools.googleapis.com/v1alpha1/claims:search",
            params={"query": query[:200], "key": GOOGLE_FC_API_KEY, "pageSize": 5},
            timeout=6)
        results = []
        for item in r.json().get("claims", []):
            review = item.get("claimReview", [{}])[0]
            rating = review.get("textualRating", "")
            rl     = rating.lower()
            rating_type = (
                "false" if any(w in rl for w in ["false","fake","misleading","pants on fire","incorrect","fabricated","wrong"])
                else "true" if any(w in rl for w in ["true","correct","accurate","verified","mostly true"])
                else "mixed"
            )
            publisher = review.get("publisher", {}).get("name", "")
            results.append({
                "claim":       item.get("text", "")[:200],
                "claimant":    item.get("claimant", "Unknown"),
                "rating":      rating,
                "rating_type": rating_type,
                "url":         review.get("url", ""),
                "publisher":   publisher,
                "favicon":     get_source_favicon(publisher),
                "initials":    get_source_initials(publisher),
            })
        return results[:5]
    except:
        return []


# ─── AI Verdicts ──────────────────────────────────────────────────────────────

def _build_prompt(claim_text, prediction, fake_prob, real_prob):
    return f"""You are a professional fact-checker and misinformation analyst.

Analyze this news claim:

CLAIM: "{claim_text}"

A scoring system assessed this as: {prediction} ({fake_prob}% fake probability, {real_prob}% real probability)

Respond ONLY in this exact JSON format, no markdown, no extra text:
{{
  "verdict": "LIKELY FAKE" or "LIKELY REAL" or "UNCERTAIN" or "MISLEADING",
  "confidence": <number 0-100>,
  "reasoning": "<2-3 sentence explanation>",
  "red_flags": ["<flag1>", "<flag2>"] or [],
  "credibility_signals": ["<signal1>"] or [],
  "recommendation": "<one sentence advice>"
}}"""

def _parse_verdict(raw):
    if not raw:
        return {}
    raw = re.sub(r'```(?:json)?\s*', '', raw)
    raw = re.sub(r'```\s*', '', raw).strip()
    match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', raw, re.DOTALL)
    if not match:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return {}
    json_str = match.group(0)
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        try:
            json_str = re.sub(r',(\s*\n\s*[}\]])', r'\1', json_str)
            parsed = json.loads(json_str)
        except:
            return {}
    v = str(parsed.get("verdict", "UNCERTAIN")).upper().strip()
    if "FAKE" in v:
        v = "LIKELY FAKE"
    elif "REAL" in v or "TRUE" in v or "ACCURATE" in v:
        v = "LIKELY REAL"
    elif "MISLEAD" in v:
        v = "MISLEADING"
    else:
        v = "UNCERTAIN"
    parsed["verdict"] = v
    parsed["verdict_type"] = (
        "fake" if "FAKE" in v else "real" if "REAL" in v else
        "misleading" if "MISLEAD" in v else "uncertain"
    )
    parsed.setdefault("red_flags", [])
    parsed.setdefault("credibility_signals", [])
    parsed.setdefault("recommendation", "")
    parsed.setdefault("reasoning", "")
    parsed.setdefault("confidence", 50)
    try:
        parsed["confidence"] = max(0, min(100, int(parsed["confidence"])))
    except:
        parsed["confidence"] = 50
    return parsed

def fetch_groq_verdict(claim_text, prediction, fake_prob, real_prob):
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": _build_prompt(claim_text, prediction, fake_prob, real_prob)}],
                "temperature": 0.1, "max_tokens": 600,
                "response_format": {"type": "json_object"},
            }, timeout=15)
        data = r.json()
        if r.status_code != 200 or "choices" not in data:
            return {}
        result = _parse_verdict(data["choices"][0]["message"]["content"])
        if not result:
            return {}
        result["provider"] = "Groq Llama 3.3-70b"
        result["provider_icon"] = "groq"
        return result
    except Exception:
        return {}

def fetch_cohere_verdict(claim_text, prediction, fake_prob, real_prob):
    try:
        r = requests.post(
            "https://api.cohere.com/v2/chat",
            headers={"Authorization": f"Bearer {COHERE_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "command-r7b-12-2024",
                "messages": [{"role": "user", "content": _build_prompt(claim_text, prediction, fake_prob, real_prob)}],
                "temperature": 0.1, "max_tokens": 600,
            }, timeout=18)
        data = r.json()
        if r.status_code != 200:
            return {}
        raw = data.get("message", {}).get("content", [{}])
        if isinstance(raw, list):
            raw = raw[0].get("text", "") if raw else ""
        elif not isinstance(raw, str):
            raw = str(raw)
        result = _parse_verdict(raw)
        if not result:
            return {}
        result["provider"] = "Cohere Command R"
        result["provider_icon"] = "cohere"
        return result
    except Exception:
        return {}

def build_consensus(verdicts):
    active = [v for v in verdicts if v]
    if not active:
        return {}
    verdict_counts = Counter(v.get("verdict_type", "uncertain") for v in active)
    top_type  = verdict_counts.most_common(1)[0][0]
    top_count = verdict_counts.most_common(1)[0][1]
    avg_conf  = round(sum(v.get("confidence", 50) for v in active) / len(active))
    verdict_label = {
        "fake": "LIKELY FAKE", "real": "LIKELY REAL",
        "misleading": "MISLEADING", "uncertain": "UNCERTAIN",
    }.get(top_type, "UNCERTAIN")
    total = len(active)
    agreement = (f"All {total} AIs agree" if top_count == total
                 else f"{top_count}/{total} AIs agree" if top_count > total / 2
                 else "AIs are divided")
    return {
        "verdict": verdict_label, "verdict_type": top_type,
        "confidence": avg_conf, "agreement": agreement,
        "agree_count": top_count, "total": total,
        "providers": [v.get("provider", "") for v in active],
    }


# ─── Pattern Analysis ─────────────────────────────────────────────────────────

def analyze_patterns(text):
    text_lower = text.lower()
    suspicious, credible = [], []
    for pattern, label in SUSPICIOUS_PATTERNS:
        if re.search(pattern, text_lower) and label not in suspicious:
            suspicious.append(label)
    for pattern, label in CREDIBLE_PATTERNS:
        if re.search(pattern, text_lower) and label not in credible:
            credible.append(label)
    return suspicious, credible

def credibility_analysis(prediction, fake_prob, real_prob, news_results, suspicious,
                         credible, fact_checks, kb_match_real, kb_match_fake):
    model_score = (100 - fake_prob) if prediction == "FAKE NEWS" else real_prob
    n = len(news_results)
    news_score = {0: 15, 1: 35, 2: 50, 3: 65}.get(min(n, 3), 80)
    lang_adj   = (len(credible) * 6) - (len(suspicious) * 5)
    fc_adj     = sum(-18 if fc["rating_type"] == "false" else
                     15 if fc["rating_type"] == "true" else 2 for fc in fact_checks)
    kb_adj     = 25 if kb_match_real else (-20 if kb_match_fake else 0)
    combined   = (model_score * 0.55) + (news_score * 0.25) + lang_adj + fc_adj + kb_adj
    if fake_prob > 80 and len(suspicious) >= 2:
        combined -= 15
    if real_prob > 80 and len(credible) >= 2:
        combined += 10
    if n >= 3 and fake_prob < 65:
        combined += 10
    return max(5, min(98, round(combined)))

def get_risk_level(credibility):
    if credibility >= 75:   return "LOW RISK",      "#00ff88"
    elif credibility >= 50: return "MODERATE RISK", "#ffcc00"
    elif credibility >= 25: return "HIGH RISK",      "#ff8800"
    else:                   return "CRITICAL RISK",  "#ff3355"

def build_explanation(prediction, suspicious, credible, confidence, fake_prob, real_prob,
                      n_news, fact_checks, kb_match_real, kb_match_fake):
    parts = []
    if kb_match_real:
        parts.append(f"✓ Verified real-world event in knowledge base: {kb_match_real[0]}")
    elif kb_match_fake:
        parts.append(f"⚠ Matches known misinformation pattern: {kb_match_fake[0]}")

    if prediction == "FAKE NEWS":
        parts.append(f"Scoring system flags this as FAKE with {fake_prob}% probability.")
        if suspicious:
            parts.append(f"Suspicious signals: {', '.join(suspicious)}.")
        parts.append("Cross-reference with trusted sources before sharing." if n_news < 3
                     else f"{n_news} debunking/related sources found.")
    else:
        parts.append(f"Scoring system predicts REAL with {real_prob}% probability.")
        if credible:
            parts.append(f"Credibility indicators: {', '.join(credible)}.")
        if suspicious:
            parts.append(f"Minor concerns: {', '.join(suspicious)}.")
        if n_news >= 2:
            parts.append(f"{n_news} corroborating sources found.")

    if fact_checks:
        fc_names = list({fc["publisher"] for fc in fact_checks if fc["publisher"]})
        ratings  = [fc["rating"] for fc in fact_checks if fc["rating"]]
        if fc_names:
            parts.append(f"Fact-checkers ({', '.join(fc_names[:3])}): {'; '.join(ratings[:3])}.")
    return " ".join(parts)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    fake_rate = round((stats["fake"] / stats["total"]) * 100) if stats["total"] > 0 else 0
    return templates.TemplateResponse("index.html", {
        "request": request, "stats": stats,
        "fake_rate": fake_rate, "history": scan_history[-5:][::-1],
    })

@app.post("/predict", response_class=HTMLResponse)
async def predict(request: Request, text: str = Form(...)):
    start = time.time()
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    label, fake_prob, real_prob, kb_match_real, kb_match_fake = score_claim(text)

    confidence = max(fake_prob, real_prob)
    hindi      = is_hindi(text)
    all_keywords = extract_keywords(text)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        f_related  = executor.submit(fetch_related_news,      text, label, all_keywords)
        f_fc       = executor.submit(fetch_google_factchecks, text[:200])
        f_wiki     = executor.submit(fetch_wikipedia_context, all_keywords, hindi, text,
                                     kb_match_real, kb_match_fake)
        f_nitter   = executor.submit(fetch_nitter_discussion, text, all_keywords, label)
        f_reddit   = executor.submit(fetch_reddit_posts,      text, all_keywords, label,
                                     kb_match_fake)
        f_groq     = executor.submit(fetch_groq_verdict,      text, label, fake_prob, real_prob)
        f_cohere   = executor.submit(fetch_cohere_verdict,    text, label, fake_prob, real_prob)

        related_news   = f_related.result()
        fact_checks    = f_fc.result()
        wiki_context   = f_wiki.result()
        nitter_tweets  = f_nitter.result()
        reddit_posts   = f_reddit.result()
        groq_verdict   = f_groq.result()
        cohere_verdict = f_cohere.result()

    used_urls     = {a["link"] for a in related_news}
    more_articles = fetch_more_articles(text, label, all_keywords, exclude_urls=used_urls)

    consensus = build_consensus([groq_verdict, cohere_verdict])

    suspicious, credible = analyze_patterns(text)
    credibility          = credibility_analysis(label, fake_prob, real_prob, related_news,
                                                suspicious, credible, fact_checks,
                                                kb_match_real, kb_match_fake)
    risk_level, risk_color = get_risk_level(credibility)
    explanation = build_explanation(label, suspicious, credible, confidence, fake_prob, real_prob,
                                    len(related_news), fact_checks, kb_match_real, kb_match_fake)
    elapsed = round((time.time() - start) * 1000)
    query   = urllib.parse.quote(text[:120])

    stats["total"] += 1
    if label == "FAKE NEWS":
        stats["fake"] += 1
    else:
        stats["real"] += 1

    scan_history.append({
        "text":       text[:80] + ("…" if len(text) > 80 else ""),
        "label":      label,
        "confidence": confidence,
        "time":       datetime.now().strftime("%H:%M"),
    })
    if len(scan_history) > 20:
        scan_history.pop(0)

    fake_rate = round((stats["fake"] / stats["total"]) * 100)

    for a in related_news + more_articles:
        img = a.get("image", "")
        if img and not _is_clean_image(img):
            a["image"] = _make_fallback_image(a.get("title", ""))
        elif not img:
            a["image"] = _make_fallback_image(a.get("title", ""))

    return templates.TemplateResponse("index.html", {
        "request":        request,
        "prediction":     label,
        "confidence":     confidence,
        "fake_prob":      fake_prob,
        "real_prob":      real_prob,
        "credibility":    credibility,
        "risk_level":     risk_level,
        "risk_color":     risk_color,
        "explanation":    explanation,
        "overridden":     False,
        "text":           text,
        "suspicious":     suspicious,
        "credible":       credible,
        "kb_match_real":  kb_match_real,
        "kb_match_fake":  kb_match_fake,
        "google_news":    f"https://news.google.com/search?q={query}",
        "snopes":         f"https://www.snopes.com/?s={query}",
        "politifact":     f"https://www.politifact.com/search/?q={query}",
        "altnews":        f"https://www.altnews.in/?s={urllib.parse.quote(text[:100])}",
        "related_news":   related_news,
        "more_articles":  more_articles,
        "fact_checks":    fact_checks,
        "wiki_context":   wiki_context,
        "nitter_tweets":  nitter_tweets,
        "reddit_posts":   reddit_posts,
        "groq_verdict":   groq_verdict,
        "cohere_verdict": cohere_verdict,
        "consensus":      consensus,
        "elapsed":        elapsed,
        "stats":          stats,
        "fake_rate":      fake_rate,
        "history":        scan_history[-5:][::-1],
    })

@app.get("/api/stats")
async def api_stats():
    fake_rate = round((stats["fake"] / stats["total"]) * 100) if stats["total"] > 0 else 0
    return JSONResponse({**stats, "fake_rate": fake_rate})