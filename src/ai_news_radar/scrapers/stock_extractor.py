"""
Stock name extractor - parses Chinese stock abbreviations from article text
and matches them to real stock codes via AKShare.
"""

import logging
import re

logger = logging.getLogger(__name__)

_name_cache = {}
_name_cache_loaded = False


def _load_stock_name_cache():
    global _name_cache, _name_cache_loaded
    if _name_cache_loaded:
        return
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        for _, row in df.iterrows():
            code = str(row["代码"])
            name = str(row["名称"])
            _name_cache[name] = code
            _name_cache[name.replace(" ", "")] = code
        logger.info("Stock name cache loaded: %d stocks", len(_name_cache))
    except Exception as e:
        logger.warning("Failed to load stock name cache: %s", e)
    _name_cache_loaded = True


def _normalize_abbr(name):
    # Supports both uppercase (KJ, GF) and lowercase (kj, gf) abbreviations
    abbr_map = {
        'GF': '股份', 'KJ': '科技', 'ZN': '智能',
        'JC': '集成', 'XC': '旭创', 'LY': '锂业',
        'GK': '高科', 'MY': '钼业', 'XT': '稀土',
        'ZY': '资源', 'DQ': '电气', 'DL': '电力',
        'JT': '集团', 'KG': '控股', 'YY': '医药',
        'DZ': '电子', 'TX': '通信', 'WL': '网络',
        # Lowercase variants (common in some accounts)
        'kj': '科技', 'gf': '股份', 'zn': '智能',
        'l': '利', 'd': '达', 'j': '纪',
    }
    # Try 2-letter suffix (uppercase or lowercase)
    suffix_match = re.search(r"([A-Za-z]{2})$", name)
    if suffix_match:
        prefix = name[:-2].strip()
        suffix = suffix_match.group(1)
        if suffix in abbr_map:
            return [prefix + abbr_map[suffix], prefix]
        return [prefix]
    # Try 1-letter suffix (uppercase or lowercase)
    single_match = re.search(r"([A-Za-z])$", name)
    if single_match:
        prefix = name[:-1].strip()
        suffix = single_match.group(1)
        if suffix in abbr_map:
            return [prefix + abbr_map[suffix], prefix]
        return [prefix]
    return [name]


def search_stock(text):
    _load_stock_name_cache()
    results = []
    seen = set()
    if not _name_cache:
        return results
    for m in re.finditer(r"([一-龥]{2,4}[A-Za-z]{1,2})", text):
        candidate = m.group(1)
        if candidate in seen:
            continue
        seen.add(candidate)
        start = max(0, m.start() - 15)
        end = min(len(text), m.end() + 15)
        snippet = text[start:end].replace('\n', ' ').strip()
        for expanded in _normalize_abbr(candidate):
            if expanded in _name_cache:
                code = _name_cache[expanded]
                results.append({
                    "stock_code": code,
                    "stock_name": candidate,
                    "stock_full_name": expanded,
                    "mention_snippet": snippet,
                    "confidence": 0.9,
                    "mention_type": "mentioned",
                })
                break
            else:
                for cache_name, cache_code in _name_cache.items():
                    if expanded in cache_name or cache_name in expanded:
                        if abs(len(expanded) - len(cache_name)) <= 2:
                            results.append({
                                "stock_code": cache_code,
                                "stock_name": candidate,
                                "stock_full_name": cache_name,
                                "mention_snippet": snippet,
                                "confidence": 0.7,
                                "mention_type": "mentioned",
                            })
                            break
    for m in re.finditer(r"(?:sh|sz)?(\d{6})", text):
        code_full = m.group(0)
        if code_full in seen:
            continue
        seen.add(code_full)
        start = max(0, m.start() - 10)
        end = min(len(text), m.end() + 10)
        snippet = text[start:end].replace('\n', ' ').strip()
        from ..utils.helpers import normalize_stock_code
        norm_code = normalize_stock_code(code_full)
        results.append({
            "stock_code": norm_code,
            "stock_name": code_full,
            "stock_full_name": code_full,
            "mention_snippet": snippet,
            "confidence": 1.0,
            "mention_type": "mentioned",
        })
    return results


def extract_sections(text):
    sections = []
    section_pattern = re.compile(
        r'(?:^|\n)\s*[一二三四五六七八九十]+[.、．]\s*([^\n]{1,30})'
    )
    parts = section_pattern.split(text)
    if len(parts) <= 1:
        stocks = search_stock(text)
        sections.append({"section": "全文", "text": text, "stocks": stocks})
    else:
        if parts[0].strip():
            stocks = search_stock(parts[0])
            sections.append({"section": "前言", "text": parts[0].strip(), "stocks": stocks})
        for i in range(1, len(parts) - 1, 2):
            if i + 1 >= len(parts):
                break
            sec_title = parts[i].strip()
            sec_content = parts[i + 1].strip()
            sec_clean = re.sub(r"[（(][^）)]*[）)]", "", sec_title).strip()
            stocks = search_stock(sec_content)
            sections.append({"section": sec_clean, "text": sec_content, "stocks": stocks})
    return sections
