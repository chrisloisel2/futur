"""
Advanced metadata extraction pipeline
"""

import logging
import re
from datetime import datetime
from typing import List, Dict, Optional


class MetadataExtractionPipeline:
    """Extract advanced metadata from items"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # Entity patterns
        self.crypto_entities = {
            'Bitcoin': ['Bitcoin', 'BTC', r'\bBitcoin\b'],
            'Ethereum': ['Ethereum', 'ETH', r'\bEthereum\b'],
            'Tether': ['Tether', 'USDT'],
            'Solana': ['Solana', 'SOL'],
            'XRP': ['XRP', 'Ripple'],
            'Cardano': ['Cardano', 'ADA'],
            'Dogecoin': ['Dogecoin', 'DOGE'],
            'Polygon': ['Polygon', 'MATIC'],
            'BNB': ['BNB', 'Binance Coin'],
        }

        self.institutional_entities = [
            'BlackRock', 'Fidelity', 'Grayscale', 'MicroStrategy',
            'Coinbase', 'Binance', 'Kraken', 'Gemini',
            'JPMorgan', 'Goldman Sachs', 'Morgan Stanley',
            'SEC', 'CFTC', 'Federal Reserve', 'Fed'
        ]

        self.event_patterns = {
            'regulation': [r'\bregulat', r'\bban\b', r'\bapproval\b', r'\bSEC\b', r'\bCFTC\b'],
            'hack': [r'\bhack', r'\bexploit', r'\bbreach', r'\bstolen'],
            'partnership': [r'\bpartnership\b', r'\bcollaborat', r'\bintegrat'],
            'lawsuit': [r'\blawsuit\b', r'\bsue', r'\blegal action'],
            'monetary_policy': [r'\binterest rate', r'\bFed\b', r'\bECB\b', r'\binflation'],
            'approval': [r'\bETF\b.*\bapproved\b', r'\bapproval\b'],
            'rejection': [r'\breject', r'\bdenied'],
        }

        self.sentiment_patterns = {
            'bullish': [r'\bbullish\b', r'\bmoon', r'\bpump', r'\bgains?\b', r'\bpositive outlook'],
            'bearish': [r'\bbearish\b', r'\bcrash', r'\bdump', r'\blosses?\b', r'\bnegative outlook'],
        }

    def process_item(self, item, spider):
        """Extract metadata from item"""
        text = self._get_full_text(item)

        # Extract crypto entities
        item['crypto_entities'] = self._extract_crypto_entities(text)

        # Extract institutional entities
        item['institutional_entities'] = self._extract_institutional_entities(text)

        # Detect event types
        item['event_types'] = self._detect_event_types(text)

        # Extract sentiment
        sentiment_data = self._extract_sentiment(text)
        item['sentiment'] = sentiment_data['sentiment']
        item['sentiment_score'] = sentiment_data['score']

        # Extract keywords
        item['keywords'] = self._extract_keywords(text)

        # Calculate credibility score
        item['credibility_score'] = self._calculate_credibility(item)

        return item

    def _get_full_text(self, item) -> str:
        """Get full text from item"""
        parts = []
        if item.get('title'):
            parts.append(item['title'])
        if item.get('body'):
            parts.append(item['body'])
        if item.get('summary'):
            parts.append(item['summary'])
        return ' '.join(parts)

    def _extract_crypto_entities(self, text: str) -> List[str]:
        """Extract mentioned crypto entities"""
        entities = []
        text_lower = text.lower()

        for entity_name, patterns in self.crypto_entities.items():
            for pattern in patterns:
                if isinstance(pattern, str) and pattern.lower() in text_lower:
                    entities.append(entity_name)
                    break
                elif hasattr(pattern, 'search') and re.search(pattern, text, re.IGNORECASE):
                    entities.append(entity_name)
                    break

        return list(set(entities))

    def _extract_institutional_entities(self, text: str) -> List[str]:
        """Extract mentioned institutional entities"""
        entities = []

        for entity in self.institutional_entities:
            if entity.lower() in text.lower():
                entities.append(entity)

        return entities

    def _detect_event_types(self, text: str) -> List[str]:
        """Detect event types in text"""
        detected_events = []

        for event_type, patterns in self.event_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    detected_events.append(event_type)
                    break

        return detected_events

    def _extract_sentiment(self, text: str) -> Dict[str, any]:
        """Extract sentiment from text"""
        bullish_count = 0
        bearish_count = 0

        for pattern in self.sentiment_patterns['bullish']:
            bullish_count += len(re.findall(pattern, text, re.IGNORECASE))

        for pattern in self.sentiment_patterns['bearish']:
            bearish_count += len(re.findall(pattern, text, re.IGNORECASE))

        total = bullish_count + bearish_count

        if total == 0:
            return {'sentiment': 'neutral', 'score': 0.0}

        score = (bullish_count - bearish_count) / total

        if score > 0.2:
            sentiment = 'bullish'
        elif score < -0.2:
            sentiment = 'bearish'
        else:
            sentiment = 'neutral'

        return {'sentiment': sentiment, 'score': score}

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract important keywords"""
        # Simple keyword extraction
        words = re.findall(r'\b[A-Z][a-z]+\b', text)
        word_freq = {}

        for word in words:
            if len(word) > 4:  # Only significant words
                word_freq[word] = word_freq.get(word, 0) + 1

        # Return top 10 keywords
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in sorted_words[:10]]

    def _calculate_credibility(self, item) -> float:
        """Calculate credibility score based on source tier and metadata"""
        score = 0.5  # Base score

        # Source tier bonus
        source_tier = item.get('source_tier', 'unknown')
        tier_scores = {
            'tier1': 1.0,
            'tier2': 0.8,
            'tier3': 0.6,
            'tier4': 0.4,
            'unknown': 0.5
        }
        score = tier_scores.get(source_tier, 0.5)

        # Bonus for official sources
        if item.get('source', '').lower() in ['sec', 'cftc', 'federal reserve', 'ecb']:
            score = 1.0

        # Penalty for missing author
        if not item.get('author'):
            score *= 0.9

        # Penalty for short content
        body_length = len(item.get('body', ''))
        if body_length < 200:
            score *= 0.8

        return round(max(0.0, min(1.0, score)), 2)
