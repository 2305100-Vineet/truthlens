# app.py
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import urllib.parse
from urllib.parse import urljoin, urlparse
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

# ── Thumbnail cache ──────────────────────────────────────────────────────────
THUMBNAIL_CACHE = {}
CACHE_EXPIRY_SECONDS = 3600  # 1 hour

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

HINDI_NEWS_DOMAINS = (
    "aajtak.in,zeenews.india.com,abplive.com,ndtv.com,ndtv.in,"
    "bhaskar.com,jagran.com,amarujala.com,news18.com,"
    "hindustantimes.com,indiatoday.in,thehindu.com,"
    "navbharattimes.indiatimes.com,livehindustan.com,"
    "jansatta.com,patrika.com,punjabkesari.in"
)

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
    "war","world","won","win","wins","beat","beats","south",
    "north","east","west","country","nation","state","city","new",
    "election","electoral","college","vote","voting","votes","voters",
    "presidential","president","prime","minister","cup",
    "final","finals","match","game","games","team","teams","player",
    "players","says","said","tells","told","man","men","woman","women",
    "first","last","next","top","best","big","biggest","huge","massive",
}

# ─────────────────────────────────────────────────────────────────────────────
# ══ ROBUST THUMBNAIL EXTRACTION SYSTEM ══════════════════════════════════════
# Priority: og:image/og:image:secure_url → twitter:image → JSON-LD → largest img
# ─────────────────────────────────────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Patterns in image URLs/alt text indicating non-article images
_SKIP_PATTERNS = [
    'logo', 'icon', 'sprite', 'favicon', 'avatar', 'placeholder',
    'blank', 'pixel', 'spacer', 'badge', 'button', 'profile',
    'default-image', 'no-image', 'noimage', 'missing', 'author',
    'generic', 'banner-ad', 'advertisement', 'tracking', 'beacon',
    'subscribe', 'newsletter', 'social', 'share', 'follow',
    'google.com/s2', 'news.google.com', 'gravatar', 'disqus',
]

# Domains that always serve generic/stock OG images — skip scraping
_SKIP_SCRAPE_DOMAINS = {
    "ft.com", "wsj.com", "bloomberg.com", "britannica.com",
    "statista.com", "pewresearch.org", "cfr.org", "un.org",
    "worldbank.org", "imf.org", "brookings.edu", "rand.org",
    "foreignaffairs.com", "pbs.org", "newyorker.com",
    "history.com", "thoughtco.com", "worldatlas.com",
}


def _is_valid_image_url(src: str, base_url: str = "") -> str:
    """
    Validates and normalises an image URL.
    - Converts relative URLs to absolute using base_url
    - Skips SVGs, data URIs, known bad patterns
    - Returns cleaned URL string or "" if invalid
    """
    if not src:
        return ""
    src = src.strip()

    # Convert protocol-relative
    if src.startswith("//"):
        src = "https:" + src

    # Convert relative URLs to absolute
    if base_url and not src.startswith("http"):
        src = urljoin(base_url, src)

    if not src.startswith("http"):
        return ""

    sl = src.lower()

    # Skip SVGs (usually icons/logos)
    if sl.endswith(".svg") or ".svg?" in sl or ".svg#" in sl:
        return ""

    # Skip data URIs
    if sl.startswith("data:"):
        return ""

    # Skip known bad patterns
    if any(skip in sl for skip in _SKIP_PATTERNS):
        return ""

    # Skip tiny images embedded in URL dimensions (e.g. /48x48/ or _32x32.)
    dm = re.search(r'[/_\-](\d{1,3})x(\d{1,3})[/_\-.]', src)
    if dm:
        w, h = int(dm.group(1)), int(dm.group(2))
        if w < 200 or h < 150:
            return ""

    return src


def _get_image_score(img_tag, base_url: str) -> tuple:
    """
    Scores an <img> tag for likelihood of being the article's hero image.
    Returns (score, src_url). Higher score = better candidate.
    """
    src = (
        img_tag.get("src") or
        img_tag.get("data-src") or
        img_tag.get("data-lazy-src") or
        img_tag.get("data-original") or
        img_tag.get("data-srcset", "").split()[0] or
        img_tag.get("srcset", "").split()[0] or
        ""
    )
    src = _is_valid_image_url(src, base_url)
    if not src:
        return (0, "")

    score = 10  # base score

    # Explicit width/height — bigger = better
    try:
        w = int(img_tag.get("width") or 0)
        h = int(img_tag.get("height") or 0)
        if w and h:
            if w < 200 or h < 150:
                return (0, "")  # too small
            score += min(w * h // 10000, 50)  # up to +50 for large images
    except (ValueError, TypeError):
        pass

    # Article-related URL fragments boost score
    ARTICLE_HINTS = (
        "article", "news", "story", "content", "media", "photo",
        "image", "upload", "cdn", "img", "picture", "featured",
    )
    sl = src.lower()
    if any(hint in sl for hint in ARTICLE_HINTS):
        score += 20

    # Alt text quality
    alt = (img_tag.get("alt") or "").lower()
    if alt and len(alt) > 5 and not any(skip in alt for skip in _SKIP_PATTERNS):
        score += 10

    # Class name hints
    cls = " ".join(img_tag.get("class") or []).lower()
    GOOD_CLASSES = ("featured", "hero", "article", "post", "thumbnail", "cover", "main")
    BAD_CLASSES  = ("logo", "icon", "avatar", "author", "social", "ad", "sponsor")
    if any(c in cls for c in GOOD_CLASSES):
        score += 15
    if any(c in cls for c in BAD_CLASSES):
        return (0, "")

    # Penalise very short URLs (likely generated placeholders)
    if len(src) < 30:
        score -= 5

    return (score, src)


def extract_thumbnail(url: str) -> str:
    """
    Robust article thumbnail extractor.

    Priority order:
      1. og:image:secure_url  (most reliable)
      2. og:image
      3. twitter:image:src / twitter:image
      4. JSON-LD  (schema.org Article/NewsArticle image field)
      5. Best scored <img> in article body

    Features:
      - Caches results for 1 hour (THUMBNAIL_CACHE)
      - Converts relative → absolute URLs (urljoin)
      - Skips logos/icons/avatars/sprites/tiny images
      - 5-second timeout, proper User-Agent
      - Safe fallback: returns "" (caller decides placeholder)
    """
    if not url or not url.startswith("http"):
        return ""

    # ── Cache check ───────────────────────────────────────────────────────
    cached = THUMBNAIL_CACHE.get(url)
    if cached and (time.time() - cached["ts"]) < CACHE_EXPIRY_SECONDS:
        return cached.get("image", "")

    def _store(img: str) -> str:
        THUMBNAIL_CACHE[url] = {"image": img, "ts": time.time()}
        return img

    # ── Skip known paywalled / stock-image domains ────────────────────────
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        if host in _SKIP_SCRAPE_DOMAINS or any(host.endswith("." + d) for d in _SKIP_SCRAPE_DOMAINS):
            return _store("")
    except Exception:
        pass

    # ── Fetch HTML ────────────────────────────────────────────────────────
    try:
        resp = requests.get(
            url,
            headers=_BROWSER_HEADERS,
            timeout=5,
            stream=True,
            allow_redirects=True,
        )
        if resp.status_code not in (200, 203):
            return _store("")
        ct = resp.headers.get("Content-Type", "")
        if ct and "html" not in ct.lower():
            return _store("")

        # Read up to 400 KB — enough for <head> + early <body>
        raw = b""
        for chunk in resp.iter_content(chunk_size=8192):
            raw += chunk
            if len(raw) >= 400_000:
                break

        html = raw.decode("utf-8", errors="replace")
        base_url = resp.url  # final URL after redirects

    except Exception:
        return _store("")

    # ── Parse HTML once ───────────────────────────────────────────────────
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return _store("")

    # ── PRIORITY 1 & 2: Open Graph ────────────────────────────────────────
    for prop in ("og:image:secure_url", "og:image"):
        tag = soup.find("meta", property=prop)
        if tag:
            src = _is_valid_image_url((tag.get("content") or "").strip(), base_url)
            if src:
                return _store(src)

    # ── PRIORITY 3: Twitter Card ──────────────────────────────────────────
    for name in ("twitter:image:src", "twitter:image"):
        tag = soup.find("meta", attrs={"name": name})
        if tag:
            src = _is_valid_image_url((tag.get("content") or "").strip(), base_url)
            if src:
                return _store(src)

    # ── PRIORITY 4: JSON-LD (schema.org) ─────────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw_json = script.string or ""
            if not raw_json.strip():
                continue
            data = json.loads(raw_json)
            # JSON-LD can be a list or a dict
            items = data if isinstance(data, list) else [data]
            for item in items:
                # Handle @graph wrapper
                if "@graph" in item:
                    items.extend(item["@graph"])
                    continue
                img = item.get("image") or item.get("thumbnailUrl") or ""
                if isinstance(img, dict):
                    img = img.get("url", "")
                elif isinstance(img, list):
                    img = img[0] if img else ""
                    if isinstance(img, dict):
                        img = img.get("url", "")
                src = _is_valid_image_url(str(img).strip(), base_url)
                if src:
                    return _store(src)
        except Exception:
            continue

    # ── PRIORITY 5: Best scored <img> in article body ─────────────────────
    # Look inside article/main containers first, then whole body
    containers = (
        soup.find_all("article") or
        soup.find_all("main") or
        [soup.find(id=re.compile(r"(content|article|story|body|post)", re.I))] or
        [soup.body]
    )
    containers = [c for c in containers if c]

    best_score, best_src = 0, ""
    for container in containers[:3]:
        for img_tag in container.find_all("img")[:40]:
            score, src = _get_image_score(img_tag, base_url)
            if score > best_score:
                best_score = score
                best_src = src

    if best_src and best_score >= 15:
        return _store(best_src)

    return _store("")


# ─────────────────────────────────────────────────────────────────────────────
# ══ TOPIC-BASED FALLBACK IMAGE (for when scraping yields nothing) ═══════════
# Uses deterministic Wikimedia URLs — topic-relevant, permanent, free
# ─────────────────────────────────────────────────────────────────────────────

_TOPIC_FALLBACKS = {
    "war": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4f/Kharkiv_after_Russian_bombardment%2C_2022.jpg/800px-Kharkiv_after_Russian_bombardment%2C_2022.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/Flag_of_Ukraine.svg/800px-Flag_of_Ukraine.svg.png",
    ],
    "mideast": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/Flag_of_Israel.svg/800px-Flag_of_Israel.svg.png",
    ],
    "politics": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/US_Capitol_Building_at_night_Jan_2006.jpg/800px-US_Capitol_Building_at_night_Jan_2006.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/Donald_Trump_official_portrait.jpg/800px-Donald_Trump_official_portrait.jpg",
    ],
    "space": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b4/Chandrayaan-3_spacecraft.jpg/800px-Chandrayaan-3_spacecraft.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/FullMoon2010.jpg/800px-FullMoon2010.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/Milky_Way_Arch.jpg/800px-Milky_Way_Arch.jpg",
    ],
    "sports": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/Cricket_pictogram.svg/800px-Cricket_pictogram.svg.png",
    ],
    "tech": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/0/04/ChatGPT_logo.svg/800px-ChatGPT_logo.svg.png",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/3/34/Elon_Musk_Royal_Society_%28crop2%29.jpg/800px-Elon_Musk_Royal_Society_%28crop2%29.jpg",
    ],
    "finance": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/46/Bitcoin.svg/800px-Bitcoin.svg.png",
    ],
    "health": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/8/82/SARS-CoV-2_without_background.png/800px-SARS-CoV-2_without_background.png",
    ],
    "climate": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/The_Earth_seen_from_Apollo_17.jpg/800px-The_Earth_seen_from_Apollo_17.jpg",
    ],
    "india": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Flag_of_India.svg/800px-Flag_of_India.svg.png",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c0/Narendra_Modi_-_2014_%28cropped%29.jpg/800px-Narendra_Modi_-_2014_%28cropped%29.jpg",
    ],
    "factcheck": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/3/38/Info_Simple_bw.svg/800px-Info_Simple_bw.svg.png",
    ],
    "news": [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/US_Capitol_Building_at_night_Jan_2006.jpg/800px-US_Capitol_Building_at_night_Jan_2006.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/The_Earth_seen_from_Apollo_17.jpg/800px-The_Earth_seen_from_Apollo_17.jpg",
    ],
}


def _make_fallback_image(title: str, index: int = 0) -> str:
    """Returns a topic-relevant fallback image URL based on article title."""
    t     = (title or "").lower()
    t_raw = (title or "")

    # Hindi keyword detection
    HINDI_MAP = [
        ("space",    ["इसरो", "चंद्रयान", "गगनयान", "अंतरिक्ष", "नासा", "रॉकेट", "उपग्रह"]),
        ("sports",   ["क्रिकेट", "विश्व कप", "आईपीएल", "रोहित", "विराट", "धोनी", "टी20"]),
        ("health",   ["कोरोना", "कोविड", "वैक्सीन", "टीका", "स्वास्थ्य", "वायरस", "महामारी"]),
        ("factcheck",["फेक", "झूठ", "अफवाह", "फर्जी", "फैक्ट", "भ्रामक", "माइक्रोचिप"]),
        ("tech",     ["तकनीक", "एआई", "मोबाइल", "इंटरनेट", "5जी", "डिजिटल", "साइबर"]),
        ("finance",  ["अर्थव्यवस्था", "बजट", "शेयर", "बाजार", "रुपया", "बैंक", "सेंसेक्स"]),
        ("war",      ["युद्ध", "रूस", "यूक्रेन", "हमला", "सेना", "हमास", "इजरायल", "गाजा"]),
        ("india",    ["भारत", "मोदी", "संसद", "लोकसभा", "भाजपा", "दिल्ली", "मुंबई"]),
    ]
    for topic, kws in HINDI_MAP:
        if any(kw in t_raw for kw in kws):
            imgs = _TOPIC_FALLBACKS.get(topic, _TOPIC_FALLBACKS["news"])
            return imgs[index % len(imgs)]

    # English keyword detection
    if any(k in t for k in ["ukraine", "russia", "war", "conflict", "invasion", "putin", "zelensky", "nato", "missile", "troops"]):
        topic = "war"
    elif any(k in t for k in ["israel", "gaza", "hamas", "palestine"]):
        topic = "mideast"
    elif any(k in t for k in ["trump", "biden", "election", "president", "democrat", "republican", "senate", "kamala", "capitol"]):
        topic = "politics"
    elif any(k in t for k in ["isro", "chandrayaan", "moon", "nasa", "space", "rocket", "satellite", "gaganyaan", "lunar", "astronaut"]):
        topic = "space"
    elif any(k in t for k in ["india", "modi", "delhi", "mumbai", "bjp", "parliament", "lok sabha"]):
        topic = "india"
    elif any(k in t for k in ["cricket", "ipl", "t20", "rohit", "kohli", "world cup", "sports", "olympic", "tournament"]):
        topic = "sports"
    elif any(k in t for k in ["chatgpt", "openai", "artificial", " ai", "ai ", "tech", "google", "apple", "microsoft", "musk", "tesla", "5g", "cyber", "digital", "software"]):
        topic = "tech"
    elif any(k in t for k in ["economy", "stock", "market", "bitcoin", "crypto", "inflation", "finance", "bank", "gdp", "budget", "sensex", "nifty", "rupee"]):
        topic = "finance"
    elif any(k in t for k in ["covid", "coronavirus", "vaccine", "pandemic", "virus", "health", "hospital", "doctor", "disease", "medicine", "mpox"]):
        topic = "health"
    elif any(k in t for k in ["climate", "environment", "flood", "earthquake", "cyclone", "drought", "pollution"]):
        topic = "climate"
    elif any(k in t for k in ["fake", "fact", "misinformation", "debunk", "hoax", "conspiracy", "misleading"]):
        topic = "factcheck"
    else:
        topic = "news"

    imgs = _TOPIC_FALLBACKS.get(topic, _TOPIC_FALLBACKS["news"])
    return imgs[index % len(imgs)]


def _is_clean_image(url: str) -> bool:
    """Quick check: is this URL a usable image (not favicon/tracking pixel/etc)?"""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url.startswith("http"):
        return False
    ul = url.lower()
    BAD = ["favicon", "sprite", "1x1", "pixel", "spacer", "tracking",
           "beacon", "news.google.com", "google.com/s2"]
    return not any(b in ul for b in BAD)


# ─────────────────────────────────────────────────────────────────────────────
# ══ ARTICLE IMAGE RESOLVER — integrates extract_thumbnail into pipeline ══════
# ─────────────────────────────────────────────────────────────────────────────

def resolve_article_image(newsapi_url_to_image: str, rss_img: str,
                          article_url: str, title: str, index: int = 0) -> str:
    """
    Full resolution pipeline for a single article thumbnail.

    1. NewsAPI urlToImage  → validated directly (already fetched by NewsAPI)
    2. RSS media image     → validated directly
    3. extract_thumbnail() → scrape OG/Twitter/JSON-LD/best-img from article page
    4. Topic fallback      → deterministic Wikimedia image based on title keywords

    Never returns None or empty string — always returns a usable URL.
    """
    # 1. NewsAPI image — most reliable since NewsAPI already resolved it
    if newsapi_url_to_image and _is_clean_image(newsapi_url_to_image):
        cleaned = _is_valid_image_url(newsapi_url_to_image)
        if cleaned:
            return cleaned

    # 2. RSS image
    if rss_img and _is_clean_image(rss_img):
        cleaned = _is_valid_image_url(rss_img)
        if cleaned:
            return cleaned

    # 3. Scrape the article page
    if article_url and article_url.startswith("http"):
        # Resolve redirects (Google News, t.co, etc.)
        real_url = _resolve_redirect(article_url)
        scraped  = extract_thumbnail(real_url)
        if scraped:
            return scraped

    # 4. Topic fallback
    return _make_fallback_image(title, index)


def _resolve_redirect(url: str) -> str:
    """Follow redirects for known redirect hosts (Google News, t.co, etc.)."""
    REDIRECT_HOSTS = ("news.google.com", "t.co", "bit.ly", "ow.ly",
                      "tinyurl.com", "buff.ly", "dlvr.it")
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return url

    if not any(h in host for h in REDIRECT_HOSTS):
        return url

    cache_key = "__redirect__" + url
    cached = THUMBNAIL_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < CACHE_EXPIRY_SECONDS:
        return cached.get("image") or url

    try:
        r = requests.get(url, headers=_BROWSER_HEADERS,
                         allow_redirects=True, timeout=5)
        final = r.url
        if final and "news.google.com" not in final and final.startswith("http"):
            THUMBNAIL_CACHE[cache_key] = {"image": final, "ts": time.time()}
            return final
    except Exception:
        pass
    return url


def fetch_images_parallel(articles: list, url_key: str = "link",
                          image_key: str = "image") -> None:
    """
    Parallel thumbnail resolution for a batch of articles.
    Articles that already have a valid image are skipped.
    Results are written in-place into the article dicts.
    """
    BLOCKED_DOMAINS = {"ft.com", "wsj.com"}

    def _blocked(u: str) -> bool:
        try:
            host = urlparse(u).netloc.lower().lstrip("www.")
            return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)
        except Exception:
            return False

    # Identify articles that need image resolution
    missing = [
        i for i, a in enumerate(articles)
        if not a.get(image_key) or not _is_clean_image(a.get(image_key, ""))
    ]
    scrapable = [i for i in missing if not _blocked(articles[i].get(url_key, ""))]

    if scrapable:
        max_workers = min(6, len(scrapable))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(
                    resolve_article_image,
                    articles[i].get("urlToImage", ""),
                    articles[i].get(image_key, ""),
                    articles[i].get(url_key, ""),
                    articles[i].get("title", "") or articles[i].get("desc", ""),
                    i,
                ): i
                for i in scrapable
            }
            done, _ = concurrent.futures.wait(futures, timeout=14)
            for fut in done:
                i = futures[fut]
                try:
                    img = fut.result()
                    if img:
                        articles[i][image_key] = img
                except Exception:
                    pass

    # Final pass: ensure every article has at least the fallback image
    for idx, article in enumerate(articles):
        img = article.get(image_key, "")
        if not img or not _is_clean_image(img):
            article[image_key] = _make_fallback_image(
                article.get("title", "") or article.get("desc", ""), idx
            )


def _rss_item_image(item) -> str:
    """Extract image URL from an RSS item's media tags."""
    SKIP = ['logo', 'icon', 'sprite', 'favicon', 'avatar', 'placeholder',
            'blank', 'pixel', 'spacer', '1x1', 'tracking', 'beacon',
            'news.google.com', 'google.com/s2']

    def _clean(url: str) -> str:
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
        m = re.search(r'<img[^>]+src=["\']([^"\']{10,})["\']', str(desc_el), re.I)
        if m:
            img = _clean(m.group(1))
            if img:
                return img

    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# ── EVENT DETECTION ────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

EVENT_TYPE_RULES = [
    (r'\b(us|u\.s\.|united states|american|presidential)\s+(election|elections|vote|voting)\b',
     "us_election",
     lambda t, y: "2024 United States presidential election" if "2024" in t else "United States presidential election"),
    (r'\b(election|elections|vote|voting)\b.{0,40}\b(us|u\.s\.|united states|america|american|trump|biden|harris|kamala)\b',
     "us_election",
     lambda t, y: "2024 United States presidential election" if "2024" in t else "United States presidential election"),
    (r'\b(trump|biden|harris|kamala).{0,40}\b(election|elected|wins|won|president|presidential)\b',
     "us_election",
     lambda t, y: "2024 United States presidential election"),
    (r'\b(india|indian|lok sabha|assembly)\s+(election|elections|vote|voting)\b',
     "india_election",
     lambda t, y: "2024 Indian general election" if "2024" in t else "Elections in India"),
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
    (r'\brussia\s+invade[sd]?\s+ukraine|\brussia[n]?\s+invasion\s+of\s+ukraine',
     "russia_ukraine",
     lambda t, y: "2022 Russian invasion of Ukraine"),
    (r'\b(?:russia|russian|ukraine|ukrainian|putin|zelensky|zelenskyy)\b.{0,50}\b(?:war|invasion|conflict|troops|missile|offensive)\b',
     "russia_ukraine",
     lambda t, y: "2022 Russian invasion of Ukraine"),
    (r'\brussia\s+launch\w*\s+\w+\s+ukraine|\b(?:invasion|war)\b.{0,30}\b(?:russia|ukraine)\b',
     "russia_ukraine",
     lambda t, y: "2022 Russian invasion of Ukraine"),
    (r'\bhamas\s+attack\w*\b',
     "hamas_israel",
     lambda t, y: "2023 Hamas-led attack on Israel"),
    (r'\b(?:hamas|israel|israeli|gaza|palestine|palestinian)\b.{0,40}\b(?:attack(?:ed)?|war|killed|conflict|bomb(?:ed)?|missile)\b',
     "hamas_israel",
     lambda t, y: "2023 Hamas-led attack on Israel"),
    (r'\boctober\s+7\b',
     "hamas_israel",
     lambda t, y: "2023 Hamas-led attack on Israel"),
    (r'\b(chandrayaan|gaganyaan|aditya.?l1|pslv|gslv|isro)\b',
     "isro_mission",
     lambda t, y: "Chandrayaan-3" if "chandrayaan" in t else "Gaganyaan" if "gaganyaan" in t else "Indian Space Research Organisation"),
    (r'\b(moon landing|lunar landing|moon mission).{0,30}\b(isro|india|chandrayaan)\b',
     "isro_mission",
     lambda t, y: "Chandrayaan-3"),
    (r'\b(nasa|james webb|hubble|spacex|artemis|iss|space station)\b',
     "nasa_space",
     lambda t, y: "NASA" if "nasa" in t else "James Webb Space Telescope" if "james webb" in t or "webb" in t else "SpaceX" if "spacex" in t else "NASA"),
    (r'\b(moon landing).{0,30}\b(fake|faked|hoax|kubrick|conspiracy)\b',
     "moon_conspiracy",
     lambda t, y: "Moon landing conspiracy theories"),
    (r'\b(chatgpt|openai|gpt.?4|gpt.?3|sam altman)\b',
     "openai",
     lambda t, y: "ChatGPT" if "chatgpt" in t else "Sam Altman" if "sam altman" in t else "OpenAI"),
    (r'\b(elon musk|twitter|x\.com).{0,30}\b(bought|acquired|acquisition|renamed|rebranded)\b',
     "musk_twitter",
     lambda t, y: "Acquisition of Twitter by Elon Musk"),
    (r'\b(covid|coronavirus|sars.?cov|pandemic).{0,40}\b(vaccine|microchip|5g|spread|origin)\b',
     "covid_misinfo",
     lambda t, y: "COVID-19 vaccine misinformation" if any(w in t for w in ["vaccine","microchip","chip"]) else "5G conspiracy theories" if "5g" in t else "COVID-19 pandemic"),
    (r'\b(vaccine|vaccination).{0,30}\b(microchip|chip|bill gates|tracking|5g)\b',
     "vaccine_misinfo",
     lambda t, y: "COVID-19 vaccine misinformation"),
    (r'\b(climate change|global warming).{0,30}\b(hoax|fake|real|denial|scientific)\b',
     "climate",
     lambda t, y: "Climate change denial" if any(w in t for w in ["hoax","fake","denial"]) else "Climate change"),
    (r'\b(flat earth|earth is flat)\b',
     "flat_earth",
     lambda t, y: "Flat Earth"),
    (r'\b(illuminati|new world order|deep state|chemtrail|reptilian|qanon)\b',
     "conspiracy",
     lambda t, y: "Illuminati" if "illuminati" in t else "New World Order (conspiracy theory)" if "new world order" in t else "Deep state conspiracy theory" if "deep state" in t else "Chemtrail conspiracy theory" if "chemtrail" in t else "Reptilian conspiracy theory" if "reptilian" in t else "QAnon"),
    (r'\b(alphafold|deepmind|protein folding)\b',
     "science",
     lambda t, y: "AlphaFold" if "alphafold" in t else "Google DeepMind"),
    (r'\b(apple|iphone).{0,30}\b(revenue|record|launched|release|iphone 1[5-9])\b',
     "apple",
     lambda t, y: "Apple Inc."),
    (r'\b(narendra modi|pm modi|prime minister india|bjp|congress india|lok sabha|parliament india)\b',
     "india_politics",
     lambda t, y: "Narendra Modi" if any(w in t for w in ["modi","narendra"]) else "Bharatiya Janata Party" if "bjp" in t else "Parliament of India"),
    (r'(वैक्सीन|टीका).{0,30}(माइक्रोचिप|चिप|बिल गेट्स)',
     "vaccine_misinfo",
     lambda t, y: "COVID-19 vaccine misinformation"),
    (r'(5g|5जी).{0,20}(कोरोना|covid|वायरस)',
     "5g_misinfo",
     lambda t, y: "5G conspiracy theories"),
    (r'(चंद्रयान|गगनयान|इसरो)',
     "isro_mission",
     lambda t, y: "Chandrayaan-3" if "चंद्रयान" in t else "Gaganyaan" if "गगनयान" in t else "Indian Space Research Organisation"),
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
     lambda t, y: "Narendra Modi" if "मोदी" in t else "Bharatiya Janata Party" if any(w in t for w in ["भाजपा","बीजेपी"]) else "Parliament of India"),
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
    t = text.lower()
    year_match = re.search(r'\b(20\d{2})\b', t)
    year_str = year_match.group(1) if year_match else ""
    for pattern, etype, wiki_fn in EVENT_TYPE_RULES:
        if re.search(pattern, t):
            topic = wiki_fn(t, year_str)
            return etype, topic
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# ── KEYWORD EXTRACTION ─────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

KNOWN_ENTITIES = [
    "Donald Trump", "Joe Biden", "Kamala Harris", "Barack Obama",
    "Narendra Modi", "PM Modi", "Rahul Gandhi", "Arvind Kejriwal",
    "Vladimir Putin", "Volodymyr Zelensky", "Volodymyr Zelenskyy",
    "Elon Musk", "Bill Gates", "Mark Zuckerberg", "Sam Altman",
    "Rohit Sharma", "Virat Kohli", "MS Dhoni", "Sachin Tendulkar",
    "George Soros", "Jeff Bezos", "Sundar Pichai",
    "ISRO", "NASA", "WHO", "UN", "FBI", "CIA", "IMF", "WTO",
    "OpenAI", "ChatGPT", "DeepMind", "AlphaFold",
    "Apple", "Google", "Microsoft", "Meta", "Tesla", "Twitter",
    "BJP", "Congress", "BBC", "Reuters", "CNN", "NDTV",
    "ICC", "BCCI", "IPL",
    "T20 World Cup", "ICC World Cup", "IPL", "Champions Trophy",
    "Chandrayaan", "Gaganyaan", "Aditya-L1",
    "Ukraine", "Russia", "Gaza", "Israel", "Palestine",
    "Parliament", "Supreme Court", "Lok Sabha", "Rajya Sabha",
    "White House", "Capitol Hill",
]

_ENTITY_PATTERNS = sorted(
    [(e, re.compile(r'\b' + re.escape(e) + r'\b', re.IGNORECASE)) for e in KNOWN_ENTITIES],
    key=lambda x: -len(x[0])
)

EVENT_PHRASE_PATTERNS = [
    r'\b(?:us|u\.s\.|united states|american|presidential)\s+election(?:s)?\s*(?:20\d{2})?\b',
    r'\b(?:lok sabha|assembly|india[n]?)\s+election(?:s)?\s*(?:20\d{2})?\b',
    r'\b20\d{2}\s+(?:us|indian|presidential|general)\s+election(?:s)?\b',
    r'\b(?:icc\s+)?t20\s+world\s+cup(?:\s+20\d{2})?\b',
    r'\b(?:icc\s+)?(?:cricket\s+)?world\s+cup(?:\s+20\d{2})?\b',
    r'\b(?:india|england|australia)\s+(?:vs?\.?|versus)\s+(?:india|england|australia|south africa|pakistan|new zealand)\b',
    r'\bipl\s+(?:20\d{2}|season\s+\d+|final)?\b',
    r'\brussia[n]?\s+invasion\s+of\s+ukraine\b',
    r'\brussia[n]?\s*[-–]\s*ukraine\s+war\b',
    r'\brussia\s+(?:invades?|invaded|launches?)\s+ukraine\b',
    r'\bhamas\s+attack(?:ed|s)?\s+(?:on\s+)?israel\b',
    r'\boctober\s+7\s+(?:attack|massacre|hamas)\b',
    r'\bchandrayaan[- ]?(?:3|three|2|two|1|one)?\b',
    r'\bgaganyaan\s+(?:mission|launch|crew)?\b',
    r'\bisro\s+(?:launch(?:es?|ed)?|mission|satellite)\b',
    r'\bnasa\s+(?:artemis|james\s+webb|moon|mars|launch)\b',
    r'\b(?:openai|chatgpt)\s+(?:users?|weekly|monthly|launch(?:es?)?\b)',
    r'\belon\s+musk\s+(?:buys?|bought|acquires?|acquired|twitter|x\.com)\b',
    r'\bapple\s+(?:iphone\s+\d+|revenue|record|launch(?:es?|ed)?)\b',
    r'\bmoon\s+landing\s+(?:fake|faked|hoax|conspiracy)\b',
    r'\bapollo\s+(?:11|program)\s+(?:fake|faked|hoax)?\b',
    r'\bvaccine\s+(?:microchip|chip|tracking|5g|bill\s+gates)\b',
    r'\b5g\s+(?:towers?|network)\s+(?:spread|cause[sd]?|linked)\s+(?:covid|coronavirus|cancer)\b',
    r'\bcovid\s*[-–]?\s*19\s+(?:pandemic|vaccine|origin|lab)\b',
]

_EVENT_PHRASE_COMPILED = [re.compile(p, re.IGNORECASE) for p in EVENT_PHRASE_PATTERNS]


def extract_event_phrases(text):
    found, seen = [], set()
    for pat in _EVENT_PHRASE_COMPILED:
        m = pat.search(text)
        if m:
            phrase = m.group(0).strip()
            pl = phrase.lower()
            if pl not in seen:
                seen.add(pl)
                found.append(phrase)
    return sorted(found, key=len, reverse=True)


def extract_known_entities(text):
    found, seen = [], set()
    for entity, pat in _ENTITY_PATTERNS:
        if pat.search(text):
            el = entity.lower()
            if el not in seen:
                seen.add(el)
                found.append(entity)
    return found


def extract_named_entities(text):
    entities = extract_known_entities(text)
    en_caps = re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,3})\b', text)
    seen = set(e.lower() for e in entities)
    for e in en_caps:
        el = e.lower()
        if el not in seen and el not in STOPWORDS_EN and len(el) > 3:
            words = el.split()
            if len(words) > 1 or (len(words) == 1 and len(words[0]) > 5):
                seen.add(el)
                entities.append(e)
    hi_seqs = re.findall(r'[\u0900-\u097F]+(?:\s+[\u0900-\u097F]+)*', text)
    for seq in hi_seqs:
        if len(seq) > 3:
            entities.append(seq)
    seen2, result = set(), []
    for e in sorted(entities, key=len, reverse=True):
        el = e.lower()
        if el not in seen2:
            seen2.add(el)
            result.append(e)
    return result


def extract_year(text):
    m = re.search(r'\b(20\d{2}|19\d{2})\b', text)
    return m.group(1) if m else ""


def extract_keywords(text):
    event_phrases = extract_event_phrases(text)
    entities = extract_known_entities(text)
    caps_nouns = []
    seen = set(e.lower() for e in event_phrases + entities)
    for e in re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b', text):
        el = e.lower()
        if el not in seen and el not in STOPWORDS_EN and len(el) > 3:
            seen.add(el)
            caps_nouns.append(e)
    year = extract_year(text)
    hi_words = re.findall(r'[\u0900-\u097F]{3,}', text)
    stopwords_hi = {"और","में","की","के","को","से","है","हैं","था","थी","थे","कि","यह","वह","इस","उस","जो","पर","भी","तो","हो","ने","एक","एवं","लेकिन"}
    hi_filtered = [w for w in hi_words if w not in stopwords_hi][:4]
    single_words = []
    all_seen = set(e.lower() for e in event_phrases + entities + caps_nouns)
    for w in re.findall(r'\b[a-zA-Z]{4,}\b', text):
        wl = w.lower()
        if wl not in STOPWORDS_EN and wl not in all_seen and len(wl) > 3:
            all_seen.add(wl)
            single_words.append(w)
    combined = event_phrases[:2] + entities[:3] + caps_nouns[:2]
    if year and year not in " ".join(combined):
        combined.append(year)
    combined += hi_filtered
    combined += single_words[:max(0, 6 - len(combined))]
    seen_final, result = set(), []
    for kw in combined:
        kl = kw.lower().strip()
        if kl and kl not in seen_final:
            seen_final.add(kl)
            result.append(kw)
    return result[:10]


def extract_newsapi_keywords(text):
    hi_translated = ""
    if is_hindi(text):
        hi_kw = _hindi_to_english_keywords(text)
        hi_translated = " ".join(hi_kw)
    work_text     = hi_translated if hi_translated else text
    year          = extract_year(text) or extract_year(hi_translated)
    _, event_wiki = detect_event_type(work_text) or detect_event_type(text)
    entities      = extract_known_entities(work_text) or extract_known_entities(text)
    event_phrases = extract_event_phrases(work_text) or extract_event_phrases(text)
    _people_set   = {x.lower() for x in KNOWN_ENTITIES[:22]}
    persons       = [e for e in entities if e.lower() in _people_set]
    orgs          = [e for e in entities if e.lower() not in _people_set]

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
    parts, seen    = [], set()

    def _add(s):
        sl = (s or "").strip().lower()
        if sl and sl not in seen and len(sl) > 1:
            seen.add(sl); parts.append(s.strip())

    _add(primary_person)
    _add(second_person)
    _add(primary_org)
    _add(second_org)
    _add(year)
    if event_phrases:
        ep = event_phrases[0]
        ep_words = ep.split()[:4]
        new_ep_words = [w for w in ep_words if w.lower() not in seen]
        if new_ep_words:
            _add(" ".join(new_ep_words))
    if not event_phrases and event_wiki:
        skip_ew = {"the","a","an","of","by","in","on","at","and","or","conspiracy","misinformation","theories","theory","denial"}
        ew_words = [w for w in event_wiki.split() if w.lower() not in skip_ew][:3]
        for w in ew_words:
            _add(w)
    if len(parts) < 2:
        for w in re.findall(r'\b[a-zA-Z]{4,}\b', work_text or text):
            if len(parts) >= 4:
                break
            wl = w.lower()
            if wl not in STOPWORDS_EN and wl not in seen:
                seen.add(wl); parts.append(w)
    return parts[:5]


# ═══════════════════════════════════════════════════════════════════════════════
# ── WIKIPEDIA TOPIC RESOLUTION ─────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_wiki_topic(claim_keywords, text):
    text_lower = text.lower()
    event_type, event_wiki = detect_event_type(text)
    if event_wiki:
        return event_wiki
    text_clean    = re.sub(r'[^\w\s]', ' ', text_lower)
    words_in_text = set(text_clean.split())
    best_topic, best_score = "", 0.0
    for key, topic in WIKI_TOPIC_MAP.items():
        if any('\u0900' <= c <= '\u097F' for c in key):
            if key in text:
                score = 2.0 + len(key) * 0.05
                if score > best_score:
                    best_score = score; best_topic = topic
            continue
        key_words = key.split()
        key_len   = len(key_words)
        matched   = sum(1 for w in key_words if w in words_in_text)
        if matched == 0:
            continue
        match_ratio = matched / key_len
        min_ratio   = 0.6 if key_len <= 2 else 0.7 if key_len <= 4 else 0.80
        if match_ratio < min_ratio:
            continue
        length_bonus   = key_len * 0.25
        full_bonus     = 0.80 if match_ratio == 1.0 else 0.0
        single_penalty = -0.40 if key_len == 1 else 0.0
        positions      = [text_lower.find(w) for w in key_words if w in text_lower]
        position_bonus = max(0.0, (200 - min((p for p in positions if p >= 0), default=200)) / 200) * 0.30 if positions else 0.0
        score = match_ratio + length_bonus + full_bonus + single_penalty + position_bonus
        if score > best_score:
            best_score = score; best_topic = topic
    if best_topic:
        return best_topic
    entities = extract_known_entities(text)
    if entities:
        def _pos(e):
            p = text_lower.find(e.lower())
            return p if p >= 0 else len(text)
        for entity in sorted(entities, key=_pos)[:2]:
            el = entity.lower()
            if el in WIKI_TOPIC_MAP:
                return WIKI_TOPIC_MAP[el]
            for key, topic in WIKI_TOPIC_MAP.items():
                if el in key or key in el:
                    return topic
    if claim_keywords:
        for kw in claim_keywords[:3]:
            kl = kw.lower()
            if kl in WIKI_TOPIC_MAP:
                return WIKI_TOPIC_MAP[kl]
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# ── TWITTER QUERY GENERATION ───────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def generate_twitter_query(text, all_keywords, prediction):
    t_lower = text.lower()
    year    = extract_year(text)
    event_type, event_wiki = detect_event_type(text)
    all_entities  = extract_known_entities(text)
    event_phrases = extract_event_phrases(text)
    _people_set   = {x.lower() for x in KNOWN_ENTITIES[:22]}
    persons = [e for e in all_entities if e.lower() in _people_set]
    orgs    = [e for e in all_entities if e.lower() not in _people_set]

    def _first_mentioned(entity_list):
        best_e, best_pos = None, len(text) + 1
        for e in entity_list:
            pos = t_lower.find(e.lower())
            if pos != -1 and pos < best_pos:
                best_pos = pos; best_e = e
        return best_e

    primary_person = _first_mentioned(persons)
    primary_org    = _first_mentioned(orgs)
    primary        = primary_person or primary_org or ""
    event_ctx      = ""
    if event_phrases:
        event_ctx = event_phrases[0]
    elif event_wiki:
        wiki_words = [w for w in event_wiki.split() if w.lower() not in {"the","a","an","of","by","in","on","at","and","or"}]
        event_ctx  = " ".join(wiki_words[:5])
    if not primary and event_wiki:
        wiki_words = [w for w in event_wiki.split() if w.lower() not in {"the","a","an","of","by","in","on","at","and","or"}]
        primary    = " ".join(wiki_words[:3])
    if not primary and all_keywords:
        en_kw   = [k for k in all_keywords if all(ord(c) < 128 for c in k)]
        primary = " ".join(en_kw[:2])
    second_person = next((e for e in persons if e != primary_person), None)
    second_org    = next((e for e in orgs   if e != primary_org),    None)

    def _dedup(*parts):
        seen_w, out = set(), []
        for part in parts:
            if not part:
                continue
            for w in part.split():
                if w.lower() not in seen_w:
                    seen_w.add(w.lower()); out.append(w)
        return " ".join(out).strip()

    raw_queries = [_dedup(primary, event_ctx, year)]
    if primary and year:
        raw_queries.append(_dedup(primary, year))
    elif primary and second_person:
        raw_queries.append(_dedup(primary, second_person))
    elif primary:
        raw_queries.append(primary)
    if event_wiki and year:
        raw_queries.append(_dedup(event_wiki[:50], year))
    elif event_ctx and year:
        raw_queries.append(_dedup(event_ctx, year))
    elif event_ctx:
        raw_queries.append(event_ctx)
    if second_person:
        raw_queries.append(_dedup(second_person, event_ctx or year))
    elif second_org and second_org != primary_org:
        raw_queries.append(_dedup(second_org, year or event_ctx))
    elif primary_org and primary_person:
        raw_queries.append(_dedup(primary_org, year))
    if prediction == "FAKE NEWS":
        raw_queries.append(_dedup(primary or event_ctx, "fact check"))
        raw_queries.append(_dedup(primary or event_ctx, "debunked misinformation"))
    else:
        raw_queries.append(_dedup(primary, second_person or second_org or primary_org, year))
        if event_wiki:
            raw_queries.append(event_wiki[:60])
    seen_q, queries = set(), []
    for q in raw_queries:
        q = q.strip()
        if not q or len(q) < 4:
            continue
        ql = q.lower()
        if ql in seen_q or re.fullmatch(r"20\d{2}", q):
            continue
        seen_q.add(ql); queries.append(q)
    if not queries:
        kw_en   = [k for k in all_keywords if all(ord(c) < 128 for c in k)][:4]
        queries = [_dedup(*kw_en[:3]), _dedup(*kw_en[:2])]
        queries = [q for q in queries if q]
    return queries[:7]


# ═══════════════════════════════════════════════════════════════════════════════
# ── CORE SCORING ENGINE ────────────────────────────────────────────────────────
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
    suspicious_hits  = sum(1 for p, _ in SUSPICIOUS_PATTERNS if re.search(p, text_lower))
    credible_hits    = sum(1 for p, _ in CREDIBLE_PATTERNS   if re.search(p, text_lower))
    fake_raw  = 50
    fake_raw += strong_fake_hits * 22
    fake_raw -= strong_real_hits * 18
    fake_raw += suspicious_hits  * 8
    fake_raw -= credible_hits    * 7
    abs_claims = len(re.findall(r'\b(100%|proven|guaranteed|banned|suppressed|secret|exposed|shocking)\b', text_lower))
    fake_raw  += abs_claims * 5
    caps_words = len(re.findall(r'\b[A-Z]{4,}\b', text))
    fake_raw  += min(caps_words * 3, 15)
    if len(text.split()) < 8:
        fake_raw += 5
    fake_raw = max(5, min(95, fake_raw))
    real_raw = 100 - fake_raw
    label    = "FAKE NEWS" if fake_raw > 50 else "REAL NEWS"
    if kb_real and kb_real[2] >= 0.6:
        if label == "FAKE NEWS" and fake_raw < 85:
            label = "REAL NEWS"; real_raw = max(real_raw, 65); fake_raw = 100 - real_raw
    if kb_fake and kb_fake[2] >= 0.6:
        if label == "REAL NEWS" and real_raw < 85:
            label = "FAKE NEWS"; fake_raw = max(fake_raw, 65); real_raw = 100 - fake_raw
    return label, fake_raw, real_raw, kb_real, kb_fake


def check_verified_event(text):
    text_lower = text.lower()
    text_clean = re.sub(r'[^\w\s]', ' ', text_lower)
    best_match, best_score = None, 0
    for phrase, label, description in VERIFIED_EVENTS_KB:
        words   = phrase.split()
        matched = sum(1 for w in words if w in text_clean)
        score   = matched / len(words)
        if score > best_score and score >= 0.6:
            best_score = score; best_match = (description, label, score)
    return best_match


def check_misinformation_kb(text):
    text_lower = text.lower()
    text_clean = re.sub(r'[^\w\s]', ' ', text_lower)
    best_match, best_score = None, 0
    for phrase, label, description in KNOWN_MISINFORMATION_KB:
        words   = phrase.split()
        matched = sum(1 for w in words if w in text_clean)
        score   = matched / len(words)
        if score > best_score and score >= 0.65:
            best_score = score; best_match = (description, label, score)
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


def is_hindi(text):
    return sum(1 for c in text if '\u0900' <= c <= '\u097F') > 5


def has_debunk_signal(text):
    t = text.lower()
    return any(sig in t for sig in DEBUNK_SIGNALS)


def time_ago(published_at):
    if not published_at:
        return ""
    try:
        pub  = datetime.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S")
        diff = datetime.utcnow() - pub
        hours = int(diff.total_seconds() // 3600)
        if hours < 1:
            return f"{int(diff.total_seconds()//60)} min ago"
        elif hours < 24:
            return f"{hours}h ago"
        else:
            d = hours // 24
            return f"{d} day{'s' if d != 1 else ''} ago"
    except Exception:
        return published_at[:10]


# ─── Wikipedia ────────────────────────────────────────────────────────────────

def fetch_wiki_image(page_title):
    SKIP = ["flag","icon","logo","symbol","edit","question","OOjs","Portal",
            "nuvola","Ambox","Wiki","commons/thumb/0","commons/thumb/f"]
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action":"query","titles":page_title,"prop":"pageimages",
                    "pithumbsize":800,"piprop":"original|thumbnail|name",
                    "format":"json","formatversion":"2"},
            timeout=7, headers={"User-Agent": "TruthLens/2.0 (educational)"})
        if r.status_code == 200:
            for page in r.json().get("query",{}).get("pages",[]):
                src = page.get("original",{}).get("source","") or page.get("thumbnail",{}).get("source","")
                if src and not any(s in src for s in SKIP):
                    return re.sub(r'/\d+px-', '/800px-', src) if "/thumb/" in src else src
    except Exception:
        pass
    try:
        encoded = urllib.parse.quote(page_title.replace(" ", "_"))
        r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}",
                         timeout=6, headers={"User-Agent": "TruthLens/2.0"})
        if r.status_code == 200:
            data = r.json()
            src  = (data.get("originalimage") or {}).get("source","") or (data.get("thumbnail") or {}).get("source","")
            if src and not any(s in src for s in SKIP):
                return re.sub(r'/\d+px-', '/800px-', src) if "/thumb/" in src else src
    except Exception:
        pass
    return ""


def fetch_wikipedia_context(claim_keywords, hindi=False, original_text="",
                            kb_match_real=None, kb_match_fake=None):
    candidates, seen_c = [], set()

    def _add(title, pref_hi=False):
        t  = (title or "").strip()
        tl = t.lower()
        if t and len(t) > 1 and tl not in seen_c:
            seen_c.add(tl); candidates.append((t, pref_hi))

    def _smart_cap(s):
        return s[0].upper() + s[1:] if s else s

    _, event_wiki_orig = detect_event_type(original_text)
    _add(event_wiki_orig, False)
    en_kw, en_text = [], ""
    if hindi or is_hindi(original_text):
        en_kw   = _hindi_to_english_keywords(original_text)
        en_text = " ".join(en_kw)
        if en_text:
            _, event_wiki_en = detect_event_type(en_text)
            _add(event_wiki_en, False)
    resolved_orig = resolve_wiki_topic(claim_keywords, original_text)
    _add(resolved_orig, False)
    if en_text:
        _add(resolve_wiki_topic(en_kw, en_text), False)
    if kb_match_fake and kb_match_fake[0]:
        kb_desc = kb_match_fake[0]
        _add(resolve_wiki_topic([], kb_desc), False)
        _add(kb_desc.split("—")[0].strip().split("/")[0].strip(), False)
    work_text = en_text if en_text else original_text
    entities  = extract_known_entities(work_text)
    if entities:
        tl_ref = work_text.lower()
        for e in sorted(entities, key=lambda e: tl_ref.find(e.lower()) if tl_ref.find(e.lower()) >= 0 else len(work_text))[:3]:
            _add(e, False)
    for ep in extract_event_phrases(work_text)[:3]:
        _add(ep, False)
    if hindi and candidates:
        _add(candidates[0][0], True)
    for fb in (["Misinformation","Fake news","Conspiracy theory"] if kb_match_fake else []) + \
              (["India","Indian media","Hindi"] if hindi or is_hindi(original_text) else []) + \
              ["Current events","News media","Journalism"]:
        _add(fb, False)

    seen_titles = set()
    for title, prefer_hindi in candidates:
        tl = title.lower().strip()
        if tl in seen_titles or len(tl) < 2:
            continue
        seen_titles.add(tl)
        apis = [WIKIPEDIA_HI_API, WIKIPEDIA_API] if prefer_hindi else [WIKIPEDIA_API]
        for api in apis:
            try:
                is_hi_api    = "hi.wikipedia" in api
                search_title = title if is_hi_api else _smart_cap(title)
                r = requests.get(api + urllib.parse.quote(search_title),
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
                    "extract":     (extract[:500] + "…" if len(extract) > 500 else extract),
                    "url":         data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    "image":       image,
                    "description": data.get("description", ""),
                }
            except Exception:
                continue
    return {}


# ─── NewsAPI ──────────────────────────────────────────────────────────────────

def _build_news_queries(claim_text, prediction, for_hindi=False):
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

    q_event = ""
    if event_wiki:
        skip_ew = {"the","a","an","of","by","in","on","at","and","or","conspiracy","misinformation","theories","theory","denial"}
        ew_words = [w for w in event_wiki.split() if w.lower() not in skip_ew]
        q_event  = " ".join(ew_words[:6])
    q_main  = _dedup_q(*kw[:4])
    q_short = _dedup_q(*kw[:3])
    q_pair  = _dedup_q(*kw[:2])
    if prediction == "FAKE NEWS":
        queries = [
            q_event + " fact check" if q_event else q_main + " fact check",
            q_event + " debunked"   if q_event else q_main + " debunked",
            q_event or q_main, q_main,
            q_short + " misinformation", q_pair,
        ]
    else:
        queries = [q_event or q_main, q_main, q_short, q_pair, (q_event or q_main) + " news"]
    if for_hindi:
        queries = [q + " india" if q and "india" not in q.lower() else q for q in queries]
    seen_q, clean = set(), []
    for q in queries:
        q = (q or "").strip(); ql = q.lower()
        if q and len(q) > 3 and ql not in seen_q:
            seen_q.add(ql); clean.append(q)
    return clean


def fetch_related_news(claim_text, prediction, all_keywords):
    hindi   = is_hindi(claim_text)
    queries = _build_news_queries(claim_text, prediction, for_hindi=False)
    articles_out, seen_urls = [], set()

    def _make_article(a, idx=0):
        url   = a.get("url", "")
        title = a.get("title") or ""
        if not url or url in seen_urls or title in ("[Removed]", "") or not title:
            return None
        seen_urls.add(url)
        desc        = a.get("description") or ""
        image       = resolve_article_image(a.get("urlToImage", ""), "", url, title, idx)
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

    if hindi:
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
                    for i, a in enumerate(data.get("articles", [])):
                        art = _make_article(a, len(articles_out))
                        if art:
                            articles_out.append(art)
                        if len(articles_out) >= 3:
                            break
                except Exception:
                    continue
        if len(articles_out) < 3:
            hi_raw  = re.findall(r'[\u0900-\u097F\s]{3,}', claim_text)
            hi_text = " ".join(hi_raw).strip()[:80] if hi_raw else ""
            for rss_q in ([hi_text] if hi_text else []) + hi_queries[:2]:
                if len(articles_out) >= 3 or not rss_q:
                    break
                try:
                    q_enc = urllib.parse.quote(rss_q)
                    r     = requests.get(
                        f"https://news.google.com/rss/search?q={q_enc}&hl=hi-IN&gl=IN&ceid=IN:hi",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                    soup  = BeautifulSoup(r.content, "xml")
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
                        real_url = _resolve_redirect(raw_url)
                        use_url  = real_url if (real_url and "news.google.com" not in real_url) else raw_url
                        if use_url in seen_urls:
                            continue
                        seen_urls.add(use_url)
                        rss_img  = _rss_item_image(item)
                        image    = resolve_article_image("", rss_img,
                                       real_url if "news.google.com" not in real_url else "",
                                       title, len(articles_out))
                        src_name = source_el.get_text(strip=True) if source_el else "Google News"
                        articles_out.append({
                            "title": title, "description": "",
                            "link": use_url, "image": image, "urlToImage": "",
                            "source": src_name, "favicon": get_source_favicon(src_name),
                            "initials": get_source_initials(src_name),
                            "published": "", "is_debunk": has_debunk_signal(title.lower()),
                        })
                except Exception:
                    pass

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
                art = _make_article(a, len(articles_out))
                if art:
                    articles_out.append(art)
                if len(articles_out) >= 6:
                    break
        except Exception:
            continue

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
                    real_url = _resolve_redirect(raw_url)
                    use_url  = real_url if (real_url and "news.google.com" not in real_url) else raw_url
                    if use_url in seen_urls:
                        continue
                    seen_urls.add(use_url)
                    rss_img  = _rss_item_image(item)
                    image    = resolve_article_image("", rss_img,
                                   real_url if "news.google.com" not in real_url else "",
                                   title, len(articles_out))
                    src_name = source_el.get_text(strip=True) if source_el else "Google News"
                    articles_out.append({
                        "title": title, "description": "",
                        "link": use_url, "image": image, "urlToImage": "",
                        "source": src_name, "favicon": get_source_favicon(src_name),
                        "initials": get_source_initials(src_name),
                        "published": "", "is_debunk": has_debunk_signal(title.lower()),
                    })
            except Exception:
                pass

    fetch_images_parallel(articles_out, url_key="link", image_key="image")
    return articles_out[:6]


def fetch_more_articles(claim_text, prediction, all_keywords, exclude_urls=None):
    if exclude_urls is None:
        exclude_urls = set()
    hindi         = is_hindi(claim_text)
    year          = extract_year(claim_text)
    _, event_wiki = detect_event_type(claim_text)
    kw            = extract_newsapi_keywords(claim_text)
    _people_set   = {x.lower() for x in KNOWN_ENTITIES[:22]}
    entities      = extract_known_entities(claim_text)
    persons       = [e for e in entities if e.lower() in _people_set]
    orgs          = [e for e in entities if e.lower() not in _people_set]
    second_person = persons[1] if len(persons) > 1 else (persons[0] if persons else "")
    primary_org   = orgs[0] if orgs else ""
    q_wiki  = ""
    if event_wiki:
        skip_ew = {"the","a","an","of","by","in","on","at","and","or","conspiracy","misinformation","theories","theory","denial"}
        q_wiki  = " ".join([w for w in event_wiki.split() if w.lower() not in skip_ew][:5])
    q_main  = " ".join(kw[:3])
    q_short = " ".join(kw[:2])
    if prediction == "FAKE NEWS":
        queries = [
            q_wiki + " false" if q_wiki else q_main + " false",
            q_main + " debunked",
            second_person + " " + (year or q_short) if second_person else q_main,
            q_wiki or q_short, q_main + " fact check",
            q_short + " hoax" if not q_wiki else q_wiki + " debunked",
        ]
    else:
        queries = [
            q_wiki or q_main,
            second_person + " " + (year or "") if second_person else q_short,
            primary_org + " " + (year or "") if primary_org else q_main,
            q_main + " explained", q_main + " latest",
            q_short + " " + (year or "news"),
        ]
    if hindi:
        hi_kw   = _hindi_to_english_keywords(claim_text)
        hi_main = " ".join(hi_kw[:3]) if hi_kw else q_main
        queries = [hi_main + " india"] + queries
    seen_q, clean_q = set(), []
    for q in queries:
        q = q.strip(); ql = q.lower()
        if q and len(q) > 3 and ql not in seen_q:
            seen_q.add(ql); clean_q.append(q)
    queries      = clean_q
    articles_out = []
    seen_urls    = set(exclude_urls)

    def _make_article(a, idx=0):
        url   = a.get("url", "")
        title = a.get("title") or ""
        if not url or url in seen_urls or title in ("[Removed]", "") or not title:
            return None
        seen_urls.add(url)
        desc        = a.get("description") or ""
        image       = resolve_article_image(a.get("urlToImage", ""), "", url, title, idx)
        source_name = (a.get("source") or {}).get("name") or ""
        published   = a.get("publishedAt") or ""
        return {
            "title":     title,
            "desc":      (desc[:160] + "…") if len(desc) > 160 else desc,
            "link":      url, "image": image, "urlToImage": a.get("urlToImage", ""),
            "source":    source_name, "favicon": get_source_favicon(source_name),
            "initials":  get_source_initials(source_name),
            "published": time_ago(published),
            "is_debunk": has_debunk_signal(title + " " + desc),
        }

    if hindi:
        hi_kw      = _hindi_to_english_keywords(claim_text)
        hi_queries = _build_news_queries(" ".join(hi_kw) if hi_kw else claim_text, prediction, for_hindi=True)
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
                        art = _make_article(a, len(articles_out))
                        if art:
                            articles_out.append(art)
                        if len(articles_out) >= 2:
                            break
                except Exception:
                    continue

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
                art = _make_article(a, len(articles_out))
                if art:
                    articles_out.append(art)
                if len(articles_out) >= 5:
                    break
        except Exception:
            continue

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
                    real_url = _resolve_redirect(raw_url)
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
                    image    = resolve_article_image("", rss_img,
                                   real_url if "news.google.com" not in real_url else "",
                                   title, len(articles_out))
                    src_name = source_el.get_text(strip=True) if source_el else "Google News"
                    articles_out.append({
                        "title": title, "desc": "",
                        "link": use_url, "image": image, "urlToImage": "",
                        "source": src_name, "favicon": get_source_favicon(src_name),
                        "initials": get_source_initials(src_name),
                        "published": t_ago, "is_debunk": has_debunk_signal(title.lower()),
                    })
            except Exception:
                pass

    fetch_images_parallel(articles_out, url_key="link", image_key="image")
    return articles_out[:5]


# ─── Twitter / Nitter ─────────────────────────────────────────────────────────

def fetch_nitter_discussion(claim_text, keywords, prediction):
    twitter_queries = generate_twitter_query(claim_text, keywords, prediction)
    tweets, seen_txt = [], set()

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
                r = requests.get(rss_url, headers={"User-Agent": "Mozilla/5.0 (compatible; TruthLens/2.0)"}, timeout=5)
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
                    url        = link_el.get_text(strip=True) if link_el else ""
                    tweet_text = ""
                    if desc_el:
                        tweet_text = re.sub(r'<[^>]+>', '', desc_el.get_text(strip=True))[:280]
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
                            t_ago = f"{int(diff.total_seconds()//60)}m" if h < 1 else f"{h}h" if h < 24 else f"{h//24}d"
                        except Exception:
                            pass
                    combined     = tweet_text.lower()
                    is_debunk    = has_debunk_signal(combined)
                    is_spreading = any(w in combined for w in ["viral","spreading","shares","retweet","trending","millions","shared"])
                    sentiment    = "debunk" if is_debunk else "spreading" if is_spreading else "neutral"
                    twitter_url  = re.sub(r'https?://(nitter\.[^/]+)', 'https://twitter.com', url) if url else ""
                    tweets.append({
                        "text": tweet_text, "username": username, "time_ago": t_ago,
                        "url": twitter_url or url, "nitter_url": url,
                        "sentiment": sentiment, "source": "nitter",
                    })
                    fetched_this_query = True
                if fetched_this_query:
                    break
            except Exception:
                continue

    if len(tweets) < 3:
        for query in twitter_queries[:3]:
            if len(tweets) >= 5:
                break
            try:
                q_enc = urllib.parse.quote(query)
                r = requests.get(f"https://rsshub.app/twitter/search/{q_enc}",
                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
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
                            "text": tweet_text, "username": username, "time_ago": t_ago,
                            "url": url, "nitter_url": url,
                            "sentiment": "debunk" if is_debunk else "neutral", "source": "nitter",
                        })
            except Exception:
                pass

    seen_search = set(t.get("search_query", "") for t in tweets)
    for query_str in twitter_queries:
        if len(tweets) >= 5:
            break
        if query_str in seen_search:
            continue
        seen_search.add(query_str)
        twitter_search_url = ("https://twitter.com/search?q=" +
                              urllib.parse.quote(query_str) + "&src=typed_query&f=live")
        tweets.append({
            "text": f"Search Twitter/X for: {query_str}", "username": "@TwitterSearch",
            "time_ago": "live", "url": twitter_search_url, "nitter_url": twitter_search_url,
            "sentiment": "neutral", "badge": "🔍 SEARCH", "source": "search",
            "search_query": query_str, "is_search_link": True,
        })

    if prediction == "FAKE NEWS":
        tweets.sort(key=lambda t: 0 if t["sentiment"] == "debunk" else 1)
    return tweets[:5]


# ─── Reddit ───────────────────────────────────────────────────────────────────

def fetch_reddit_posts(claim_text, all_keywords, prediction, kb_match_fake=None):
    """
    Robust Reddit post fetcher — always returns posts.

    Pipeline (stops as soon as we have 5 posts):
      1. Reddit JSON API  — global search, multiple User-Agents, all queries
      2. Reddit JSON API  — per-subreddit search (restrict_sr=1)
      3. Reddit RSS feed  — /search.rss (no auth needed, different rate-limit bucket)
      4. Google News RSS  — "site:reddit.com <query>" (always works)
      5. Guaranteed fallback — static clickable Reddit search links so UI never empty
    """
    kw = [k for k in all_keywords if all(ord(c) < 128 for c in k)][:5]
    if not kw:
        kw = extract_newsapi_keywords(claim_text)
    # Also pull from event detection for better queries
    _, event_wiki = detect_event_type(claim_text)
    if event_wiki and not kw:
        skip_ew = {"the","a","an","of","by","in","on","at","and","or","conspiracy","misinformation","theories","theory","denial"}
        kw = [w for w in event_wiki.split() if w.lower() not in skip_ew][:4]

    query_parts = kw[:4]
    q_main  = " ".join(query_parts[:3])
    q_short = " ".join(query_parts[:2])
    q_one   = kw[0] if kw else claim_text[:40]

    if prediction == "FAKE NEWS":
        queries = [q_main, q_short, q_one, q_main + " debunked", q_main + " fake"]
        if kb_match_fake and kb_match_fake[0]:
            kb_topic = kb_match_fake[0].split("—")[0].strip()[:60]
            if kb_topic:
                queries.insert(0, kb_topic)
        subreddits = ["worldnews", "Snopes", "skeptic", "factcheck", "politics", "news", "india", "conspiracy"]
    else:
        queries    = [q_main, q_short, q_one, q_main + " news", q_short + " latest"]
        subreddits = ["worldnews", "news", "india", "technology", "science", "cricket", "geopolitics", "investing"]

    # Remove empty/duplicate queries
    seen_q, clean_queries = set(), []
    for q in queries:
        q = q.strip()
        if q and q.lower() not in seen_q:
            seen_q.add(q.lower()); clean_queries.append(q)
    queries = clean_queries

    posts, seen_titles = [], set()

    # ── Multiple User-Agents to avoid Reddit blocking one ─────────────────
    REDDIT_UA_POOL = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "TruthLens/2.0 (educational fake-news detector; contact vineet@example.com)",
        "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    ]

    def _reddit_headers(ua_index=0):
        return {
            "User-Agent": REDDIT_UA_POOL[ua_index % len(REDDIT_UA_POOL)],
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _parse_reddit_child(child, fallback_subreddit="r/news"):
        p         = child.get("data", {})
        title     = p.get("title", "")
        permalink = p.get("permalink", "")
        url       = "https://www.reddit.com" + permalink if permalink else p.get("url", "")
        if not title or not url or title in seen_titles:
            return None
        seen_titles.add(title)
        created_utc = p.get("created_utc", 0)
        t_ago = ""
        if created_utc:
            diff = datetime.utcnow() - datetime.utcfromtimestamp(created_utc)
            h    = int(diff.total_seconds() // 3600)
            t_ago = (f"{int(diff.total_seconds()//60)}m" if h < 1
                     else f"{h}h" if h < 24 else f"{h // 24}d")
        selftext = p.get("selftext", "")[:200]
        return {
            "title":        title,
            "text":         selftext,
            "subreddit":    p.get("subreddit_name_prefixed", fallback_subreddit),
            "score":        p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
            "url":          url,
            "time_ago":     t_ago,
            "is_debunk":    has_debunk_signal((title + " " + selftext).lower()),
            "source":       "reddit",
        }

    # ── PHASE 1: Reddit JSON global search — try all queries + UA rotation ──
    for qi, q in enumerate(queries):
        if len(posts) >= 5:
            break
        for ua_i in range(2):  # try 2 different User-Agents per query
            if len(posts) >= 5:
                break
            try:
                r = requests.get(
                    f"https://www.reddit.com/search.json"
                    f"?q={urllib.parse.quote(q)}&sort=relevance&limit=20&t=year",
                    headers=_reddit_headers(qi + ua_i),
                    timeout=8,
                )
                if r.status_code == 429:
                    time.sleep(0.5)  # brief back-off on rate limit
                    continue
                if r.status_code != 200:
                    continue
                for child in r.json().get("data", {}).get("children", []):
                    if len(posts) >= 5:
                        break
                    post = _parse_reddit_child(child)
                    if post:
                        posts.append(post)
                if posts:
                    break  # got results from this query — move to next
            except Exception:
                continue

    # ── PHASE 2: Per-subreddit search ─────────────────────────────────────
    if len(posts) < 5:
        for si, sr in enumerate(subreddits):
            if len(posts) >= 5:
                break
            q = q_main or q_short or q_one
            try:
                r = requests.get(
                    f"https://www.reddit.com/r/{sr}/search.json"
                    f"?q={urllib.parse.quote(q)}&restrict_sr=1&sort=relevance&limit=10&t=year",
                    headers=_reddit_headers(si),
                    timeout=7,
                )
                if r.status_code not in (200, 429):
                    continue
                if r.status_code == 429:
                    time.sleep(0.3)
                    continue
                for child in r.json().get("data", {}).get("children", []):
                    if len(posts) >= 5:
                        break
                    post = _parse_reddit_child(child, f"r/{sr}")
                    if post:
                        posts.append(post)
            except Exception:
                continue

    # ── PHASE 3: Reddit RSS feed — different rate-limit bucket ───────────
    if len(posts) < 3:
        for q in queries[:3]:
            if len(posts) >= 5:
                break
            try:
                rss_url = f"https://www.reddit.com/search.rss?q={urllib.parse.quote(q)}&sort=relevance&limit=10"
                r = requests.get(
                    rss_url,
                    headers={"User-Agent": REDDIT_UA_POOL[2]},
                    timeout=7,
                )
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.content, "xml")
                for entry in soup.find_all("entry")[:10]:
                    if len(posts) >= 5:
                        break
                    title_el   = entry.find("title")
                    link_el    = entry.find("link")
                    content_el = entry.find("content") or entry.find("summary")
                    category_el= entry.find("category")
                    updated_el = entry.find("updated")
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)
                    url   = (link_el.get("href","") if link_el else "").strip()
                    if not url:
                        continue
                    subreddit_label = "r/" + (category_el.get("term","news") if category_el else "news")
                    t_ago = ""
                    if updated_el:
                        try:
                            dt   = datetime.strptime(updated_el.get_text(strip=True)[:19], "%Y-%m-%dT%H:%M:%S")
                            diff = datetime.utcnow() - dt
                            h    = int(diff.total_seconds() // 3600)
                            t_ago = f"{h}h" if h < 24 else f"{h//24}d"
                        except Exception:
                            pass
                    selftext = ""
                    if content_el:
                        selftext = re.sub(r'<[^>]+>', '', content_el.get_text(strip=True))[:200]
                    posts.append({
                        "title":        title,
                        "text":         selftext,
                        "subreddit":    subreddit_label,
                        "score":        0,
                        "num_comments": 0,
                        "url":          url,
                        "time_ago":     t_ago,
                        "is_debunk":    has_debunk_signal((title + " " + selftext).lower()),
                        "source":       "reddit_rss",
                    })
            except Exception:
                continue

    # ── PHASE 4: Google News RSS  "site:reddit.com <query>" ─────────────
    # This never fails — Google News always has Reddit results
    if len(posts) < 3:
        for q in queries[:2]:
            if len(posts) >= 5:
                break
            try:
                google_q = urllib.parse.quote(f"site:reddit.com {q}")
                r = requests.get(
                    f"https://news.google.com/rss/search?q={google_q}&hl=en-IN&gl=IN&ceid=IN:en",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=7,
                )
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.content, "xml")
                for item in soup.find_all("item")[:10]:
                    if len(posts) >= 5:
                        break
                    title_el  = item.find("title")
                    link_el   = item.find("link")
                    pub_el    = item.find("pubDate")
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    # Strip " - r/subreddit" suffix Google News adds
                    subreddit_label = "r/worldnews"
                    sr_match = re.search(r'[-–]\s*(r/\w+)', title)
                    if sr_match:
                        subreddit_label = sr_match.group(1)
                        title = title[:sr_match.start()].strip()
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)
                    raw_url  = link_el.get_text(strip=True) if link_el else ""
                    real_url = _resolve_redirect(raw_url) if raw_url else ""
                    use_url  = real_url if (real_url and "news.google.com" not in real_url) else raw_url
                    if not use_url:
                        continue
                    t_ago = ""
                    if pub_el:
                        try:
                            dt   = parsedate_to_datetime(pub_el.get_text(strip=True))
                            diff = datetime.utcnow() - dt.replace(tzinfo=None)
                            h    = int(diff.total_seconds() // 3600)
                            t_ago = f"{h}h" if h < 24 else f"{h//24}d"
                        except Exception:
                            pass
                    posts.append({
                        "title":        title,
                        "text":         "",
                        "subreddit":    subreddit_label,
                        "score":        0,
                        "num_comments": 0,
                        "url":          use_url,
                        "time_ago":     t_ago,
                        "is_debunk":    has_debunk_signal(title.lower()),
                        "source":       "google_reddit",
                    })
            except Exception:
                continue

    # ── PHASE 5: Guaranteed fallback — clickable Reddit search links ──────
    # UI will ALWAYS show something — never an empty Reddit section
    if len(posts) < 3:
        FALLBACK_SUBREDDITS = [
            ("r/worldnews",  "World news discussions"),
            ("r/news",       "General news discussions"),
            ("r/factcheck",  "Fact-checking community"),
            ("r/skeptic",    "Skeptics community"),
            ("r/india",      "India news & discussions"),
        ]
        for sr_name, sr_desc in FALLBACK_SUBREDDITS:
            if len(posts) >= 5:
                break
            search_q   = q_main or q_one
            search_url = (
                f"https://www.reddit.com/{sr_name}/search"
                f"?q={urllib.parse.quote(search_q)}&restrict_sr=1&sort=relevance"
            )
            fallback_title = f'Search "{search_q}" in {sr_name}'
            if fallback_title in seen_titles:
                continue
            seen_titles.add(fallback_title)
            posts.append({
                "title":        fallback_title,
                "text":         f"Click to find Reddit discussions about this topic in {sr_name} — {sr_desc}.",
                "subreddit":    sr_name,
                "score":        0,
                "num_comments": 0,
                "url":          search_url,
                "time_ago":     "",
                "is_debunk":    False,
                "source":       "fallback",
            })

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
            review    = item.get("claimReview", [{}])[0]
            rating    = review.get("textualRating", "")
            rl        = rating.lower()
            rating_type = (
                "false" if any(w in rl for w in ["false","fake","misleading","pants on fire","incorrect","fabricated","wrong"])
                else "true" if any(w in rl for w in ["true","correct","accurate","verified","mostly true"])
                else "mixed"
            )
            publisher = review.get("publisher", {}).get("name", "")
            results.append({
                "claim":       item.get("text","")[:200],
                "claimant":    item.get("claimant","Unknown"),
                "rating":      rating, "rating_type": rating_type,
                "url":         review.get("url",""),
                "publisher":   publisher,
                "favicon":     get_source_favicon(publisher),
                "initials":    get_source_initials(publisher),
            })
        return results[:5]
    except Exception:
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
            parsed   = json.loads(json_str)
        except Exception:
            return {}
    v = str(parsed.get("verdict", "UNCERTAIN")).upper().strip()
    if "FAKE" in v:       v = "LIKELY FAKE"
    elif "REAL" in v or "TRUE" in v or "ACCURATE" in v: v = "LIKELY REAL"
    elif "MISLEAD" in v:  v = "MISLEADING"
    else:                 v = "UNCERTAIN"
    parsed["verdict"]      = v
    parsed["verdict_type"] = ("fake" if "FAKE" in v else "real" if "REAL" in v else "misleading" if "MISLEAD" in v else "uncertain")
    parsed.setdefault("red_flags", [])
    parsed.setdefault("credibility_signals", [])
    parsed.setdefault("recommendation", "")
    parsed.setdefault("reasoning", "")
    parsed.setdefault("confidence", 50)
    try:
        parsed["confidence"] = max(0, min(100, int(parsed["confidence"])))
    except Exception:
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
        result["provider"]      = "Groq Llama 3.3-70b"
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
        result["provider"]      = "Cohere Command R"
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
    verdict_label = {"fake": "LIKELY FAKE", "real": "LIKELY REAL",
                     "misleading": "MISLEADING", "uncertain": "UNCERTAIN"}.get(top_type, "UNCERTAIN")
    total     = len(active)
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
    n           = len(news_results)
    news_score  = {0: 15, 1: 35, 2: 50, 3: 65}.get(min(n, 3), 80)
    lang_adj    = (len(credible) * 6) - (len(suspicious) * 5)
    fc_adj      = sum(-18 if fc["rating_type"] == "false" else 15 if fc["rating_type"] == "true" else 2 for fc in fact_checks)
    kb_adj      = 25 if kb_match_real else (-20 if kb_match_fake else 0)
    combined    = (model_score * 0.55) + (news_score * 0.25) + lang_adj + fc_adj + kb_adj
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
    text  = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    label, fake_prob, real_prob, kb_match_real, kb_match_fake = score_claim(text)
    confidence   = max(fake_prob, real_prob)
    hindi        = is_hindi(text)
    all_keywords = extract_keywords(text)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        f_related = executor.submit(fetch_related_news,      text, label, all_keywords)
        f_fc      = executor.submit(fetch_google_factchecks, text[:200])
        f_wiki    = executor.submit(fetch_wikipedia_context, all_keywords, hindi, text, kb_match_real, kb_match_fake)
        f_nitter  = executor.submit(fetch_nitter_discussion, text, all_keywords, label)
        f_reddit  = executor.submit(fetch_reddit_posts,      text, all_keywords, label, kb_match_fake)
        f_groq    = executor.submit(fetch_groq_verdict,      text, label, fake_prob, real_prob)
        f_cohere  = executor.submit(fetch_cohere_verdict,    text, label, fake_prob, real_prob)

        related_news   = f_related.result()
        fact_checks    = f_fc.result()
        wiki_context   = f_wiki.result()
        nitter_tweets  = f_nitter.result()
        reddit_posts   = f_reddit.result()
        groq_verdict   = f_groq.result()
        cohere_verdict = f_cohere.result()

    used_urls     = {a["link"] for a in related_news}
    more_articles = fetch_more_articles(text, label, all_keywords, exclude_urls=used_urls)
    consensus     = build_consensus([groq_verdict, cohere_verdict])

    suspicious, credible = analyze_patterns(text)
    credibility          = credibility_analysis(label, fake_prob, real_prob, related_news,
                                                suspicious, credible, fact_checks,
                                                kb_match_real, kb_match_fake)
    risk_level, risk_color = get_risk_level(credibility)
    explanation = build_explanation(label, suspicious, credible, confidence, fake_prob, real_prob,
                                    len(related_news), fact_checks, kb_match_real, kb_match_fake)
    elapsed   = round((time.time() - start) * 1000)
    query     = urllib.parse.quote(text[:120])

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

    # Final image safety pass — ensure every article has a valid image
    for idx, a in enumerate(related_news + more_articles):
        img = a.get("image", "")
        if not img or not _is_clean_image(img):
            a["image"] = _make_fallback_image(a.get("title", ""), idx)

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
