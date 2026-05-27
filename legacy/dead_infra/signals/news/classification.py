"""
Classification d'événements et extraction d'entités strictement structurée.
Pas de génération libre, classification fermée uniquement.
"""

import re
from typing import List, Tuple
from transformers import pipeline
import spacy

from .models import EventType, EventStatus, SurpriseLevel, RawNewsArticle
from config import EVENT_TYPES


class EventTypeClassifier:
    """Classification du type d'événement (fermée)"""

    def __init__(self):
        # Patterns pour chaque type d'événement
        self.event_patterns = {
            EventType.REGULATION: [
                r'\bregulat(ion|ory|ed|e)\b', r'\bcompliance\b', r'\blicense\b',
                r'\bframework\b', r'\brules?\b', r'\blaw\b', r'\blegislation\b'
            ],
            EventType.MONETARY_POLICY: [
                r'\binterest rate\b', r'\brate (hike|cut)\b', r'\bFOMC\b',
                r'\bmonetary policy\b', r'\bquantitative easing\b', r'\bQE\b',
                r'\bcentral bank\b', r'\bFederal Reserve\b', r'\bECB\b'
            ],
            EventType.APPROVAL: [
                r'\bapprov(al|ed|e)\b', r'\bgreen.?light\b', r'\bauthoriz(ed|ation)\b',
                r'\bpermit(ted)?\b', r'\baccept(ed)?\b', r'\bETF.*approv\b'
            ],
            EventType.REJECTION: [
                r'\breject(ed|ion)\b', r'\bden(y|ied)\b', r'\brefus(ed|al)\b',
                r'\bdeclin(ed|e)\b', r'\bturn(ed)? down\b'
            ],
            EventType.HACK: [
                r'\bhack(ed|er)?\b', r'\bbreach\b', r'\bcompromis(ed|e)\b',
                r'\bstol(en|e)\b', r'\bexploit(ed)?\b', r'\bvulnerability\b'
            ],
            EventType.EXPLOIT: [
                r'\bexploit(ed)?\b', r'\bvulnerability\b', r'\bsmart contract.*bug\b',
                r'\b(re-?)?entran(cy|t)\b', r'\bflash loan\b'
            ],
            EventType.SANCTION: [
                r'\bsanction(s|ed)?\b', r'\bpenalt(y|ies)\b', r'\bfine(d)?\b',
                r'\bembar(go|goed)\b', r'\brestriction(s)?\b'
            ],
            EventType.LAWSUIT: [
                r'\blawsuit\b', r'\bsue(d|ing)?\b', r'\blitigation\b',
                r'\blegal action\b', r'\bcourt\b', r'\bcomplaint\b'
            ],
            EventType.BANKRUPTCY: [
                r'\bbankrupt(cy)?\b', r'\binsolven(t|cy)\b', r'\bChapter (7|11)\b',
                r'\bliquidation\b', r'\bcollapse\b'
            ],
            EventType.PARTNERSHIP: [
                r'\bpartnership\b', r'\bcollaborat(ion|e)\b', r'\balliance\b',
                r'\bjoint venture\b', r'\bteam(ed|ing) up\b', r'\bintegrat(ion|e)\b'
            ],
            EventType.MACRO_DATA_RELEASE: [
                r'\bCPI\b', r'\bNFP\b', r'\bGDP\b', r'\binflation.*data\b',
                r'\bemployment.*report\b', r'\beconomic.*data\b'
            ],
            EventType.GEOPOLITICAL_CONFLICT: [
                r'\bwar\b', r'\bconflict\b', r'\binvasion\b', r'\bmilitary\b',
                r'\btension(s)?\b', r'\bcrisis\b'
            ],
            EventType.EXCHANGE_LISTING: [
                r'\blisting\b', r'\blisted\b', r'\badd(ed|ing).*trading\b',
                r'\bnow available.*exchange\b'
            ],
            EventType.DELISTING: [
                r'\bdelis(t|ting)\b', r'\bremov(ed|ing).*exchange\b',
                r'\bsuspend(ed|ing).*trading\b'
            ],
            EventType.PROTOCOL_UPGRADE: [
                r'\bupgrade\b', r'\bhard fork\b', r'\bsoft fork\b',
                r'\bnetwork.*update\b', r'\bprotocol.*change\b'
            ],
            EventType.SECURITY_BREACH: [
                r'\bsecurity.*breach\b', r'\bdata.*leak\b', r'\bunauthorized access\b',
                r'\bcyber.*attack\b'
            ],
            EventType.FRAUD_ALLEGATION: [
                r'\bfraud\b', r'\bscam\b', r'\bponzi\b', r'\bmisleading\b',
                r'\bdeceptive\b', r'\balleg(ed|ation)\b.*fraud'
            ],
            EventType.INVESTIGATION: [
                r'\binvestigat(ion|e|ing)\b', r'\bprobe\b', r'\binquiry\b',
                r'\bexamin(e|ing)\b', r'\bscrutin(y|ize)\b'
            ]
        }

    def classify(self, article: RawNewsArticle) -> List[EventType]:
        """
        Classifier type(s) d'événement
        Peut retourner plusieurs types si événement complexe
        """

        text = (article.title + " " + article.body).lower()
        detected_types = []

        for event_type, patterns in self.event_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    if event_type not in detected_types:
                        detected_types.append(event_type)
                    break

        # Si rien détecté, classification par défaut selon contexte
        if not detected_types:
            detected_types = [self._default_classification(text)]

        return detected_types

    def _default_classification(self, text: str) -> EventType:
        """Classification par défaut basée sur contexte"""

        # Crypto context
        if any(word in text for word in ["bitcoin", "crypto", "blockchain", "ethereum"]):
            if any(word in text for word in ["announce", "launch", "release"]):
                return EventType.PARTNERSHIP
            else:
                return EventType.REGULATION

        # Macro context
        if any(word in text for word in ["fed", "economy", "inflation", "gdp"]):
            return EventType.MACRO_DATA_RELEASE

        # Default
        return EventType.REGULATION


class EventStatusClassifier:
    """Classification du statut de l'information"""

    def classify(self, article: RawNewsArticle) -> EventStatus:
        """Détermine: rumor, leak, official_announcement, confirmation"""

        text = (article.title + " " + article.body[:500]).lower()

        # Patterns confirmation
        confirmation_patterns = [
            r'\bconfirm(ed|s)\b', r'\bverified\b', r'\bofficial(ly)?\b',
            r'\bannounced\b', r'\bstated\b', r'\bdeclared\b'
        ]

        for pattern in confirmation_patterns:
            if re.search(pattern, text):
                return EventStatus.CONFIRMATION

        # Patterns official announcement
        announcement_patterns = [
            r'\bannouncing\b', r'\bpress release\b', r'\bstatement\b',
            r'\bofficially\b', r'\bpublicly announced\b'
        ]

        for pattern in announcement_patterns:
            if re.search(pattern, text):
                return EventStatus.OFFICIAL_ANNOUNCEMENT

        # Patterns leak
        leak_patterns = [
            r'\bleak(ed)?\b', r'\bunofficial\b', r'\binternal.*document\b',
            r'\bsource(s)? say\b', r'\breport(edly)?\b'
        ]

        for pattern in leak_patterns:
            if re.search(pattern, text):
                return EventStatus.LEAK

        # Patterns rumor
        rumor_patterns = [
            r'\brumor(s|ed)?\b', r'\bspeculat(ion|e)\b', r'\balleged(ly)?\b',
            r'\bunconfirmed\b', r'\bmay\b', r'\bmight\b', r'\bcould\b'
        ]

        for pattern in rumor_patterns:
            if re.search(pattern, text):
                return EventStatus.RUMOR

        # Default = official announcement si source crédible
        return EventStatus.OFFICIAL_ANNOUNCEMENT


class SurpriseLevelClassifier:
    """Classification du degré de surprise"""

    def __init__(self):
        # Mots-clés surprise
        self.unexpected_keywords = [
            "shock", "surprise", "unexpected", "sudden", "abrupt",
            "unanticipated", "unforeseen", "stunning", "dramatic"
        ]

        self.expected_keywords = [
            "expected", "anticipated", "scheduled", "planned",
            "announced earlier", "as expected", "forecasted"
        ]

    def classify(self, article: RawNewsArticle) -> SurpriseLevel:
        """Détermine: expected, partially_expected, unexpected"""

        text = (article.title + " " + article.body[:300]).lower()

        # Check unexpected
        unexpected_count = sum(1 for kw in self.unexpected_keywords if kw in text)
        expected_count = sum(1 for kw in self.expected_keywords if kw in text)

        if unexpected_count > 0 and expected_count == 0:
            return SurpriseLevel.UNEXPECTED

        if expected_count > 0 and unexpected_count == 0:
            return SurpriseLevel.EXPECTED

        # Mixed ou neutre
        if unexpected_count > 0 and expected_count > 0:
            return SurpriseLevel.PARTIALLY_EXPECTED

        # Default basé sur contexte
        # Si événement majeur sans indication = partially expected
        major_keywords = ["major", "significant", "important", "key", "critical"]
        if any(kw in text for kw in major_keywords):
            return SurpriseLevel.PARTIALLY_EXPECTED

        return SurpriseLevel.EXPECTED


class NamedEntityExtractor:
    """Extraction d'entités nommées avec spaCy"""

    def __init__(self):
        # Charger modèle spaCy
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            # Fallback si modèle pas installé
            self.nlp = None

    def extract(self, article: RawNewsArticle) -> List[str]:
        """
        Extraire entités nommées pertinentes
        ORG, PERSON, GPE, MONEY, etc.
        """

        if not self.nlp:
            # Fallback: extraction basique par patterns
            return self._extract_fallback(article)

        # Combiner titre + début body
        text = article.title + " " + article.body[:1000]

        doc = self.nlp(text)

        entities = []

        for ent in doc.ents:
            # Filtrer types pertinents
            if ent.label_ in ["ORG", "PERSON", "GPE", "MONEY", "PRODUCT"]:
                if ent.text not in entities:
                    entities.append(ent.text)

        return entities

    def _extract_fallback(self, article: RawNewsArticle) -> List[str]:
        """Extraction basique sans spaCy"""
        from filters import EntityExtractor
        return EntityExtractor.extract(article)


class SemanticProcessor:
    """Orchestration de toute l'analyse sémantique"""

    def __init__(self):
        self.event_classifier = EventTypeClassifier()
        self.status_classifier = EventStatusClassifier()
        self.surprise_classifier = SurpriseLevelClassifier()
        self.entity_extractor = NamedEntityExtractor()

    def process(self, article: RawNewsArticle) -> dict:
        """
        Retourne dict avec toutes les classifications
        """

        event_types = self.event_classifier.classify(article)
        event_status = self.status_classifier.classify(article)
        surprise_level = self.surprise_classifier.classify(article)
        named_entities = self.entity_extractor.extract(article)

        return {
            "event_types": event_types,
            "event_status": event_status,
            "surprise_level": surprise_level,
            "named_entities": named_entities
        }
