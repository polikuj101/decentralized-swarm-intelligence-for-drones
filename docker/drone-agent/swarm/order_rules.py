"""swarm/order_rules.py — детерминированный разбор приказа оператора (чистые функции, без сети).

Прогон scripts/verify_tasking.py 2026-10-25: Qwen2.5-0.5B верно определяла зоны в 8 из 20 приказов, а в 10 из 20 выдавала alpha+bravo+charlie
независимо от текста (флагманский «квадрат Браво» -> сектора [0,1,4] вместо [4]). Названия зон — закрытый словарь, поэтому зоны берутся
из текста правилами, а не из модели. Поля on_target / dwell_s / rtb_on_complete правила заполняют, только если в приказе есть явные
слова; иначе None, и поле берётся из ответа модели. Решение владельца продукта 2026-10-26 (HANDOFF п. 5: правила + пост-проверка)."""
import re

ZONES = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
# основы без падежных окончаний: «Альфу», «Дельте», «Фокстроту»; «Браво», «Эхо», «Чарли» не склоняются
ZONE_STEMS = {"alpha": ("альф", "alpha"), "bravo": ("брав", "bravo"), "charlie": ("чарли", "charlie"),
              "delta": ("дельт", "delta"), "echo": ("эхо", "echo"), "foxtrot": ("фокстрот", "foxtrot")}
ALL_ZONES = re.compile(r"\bвс[её]\s+(?:квадрат|зон|сектор|район|территор|област)|\bвесь\s+(?:район|квадрат|участок)"
                       r"|\bвсю\s+(?:территор|зону|площад|област)|\beverything\b|\b(?:entire|whole)\s+(?:area|region|map|grid|zone)"
                       r"|\ball\s+(?:the\s+)?(?:zones|sectors|squares|areas)\b")

SKIP = re.compile(r"игнор|цел\w*\s+не\s+трога|не\s+трога\w*\s+цел|не\s+обращ\w*\s+внимани|пропуска\w*\s+цел|пропусти\w*\s+цел"
                  r"|\bignore|\bdisregard|\bskip\s+(?:all\s+|any\s+)?(?:targets|detections|contacts)")
DWELL_ONLY = re.compile(r"только\s+подтверд|подтверд\w*\s+(?:только\s+)?(?:их\s+)?(?:присутств|наличи)|без\s+доразвед|без\s+съ[её]мк"
                        r"|confirm\s+(?:the\s+)?presence|only\s+confirm|presence\s+of\s+any\s+targets\s+only|without\s+(?:recon|imaging|photos?)")
DWELL_RECON = re.compile(r"доразвед|зависн|зависа|пригляд|\binvestigat|\bhover|\bloiter|\bdwell")

STAY = re.compile(r"оста\w*\s+на\s+месте|оста\w*\s+в\s+(?:точке|воздухе)|удерж\w*\s+позици|не\s+возвращ|на\s+месте\s+после"
                  r"|\bstay\s+(?:in\s+place|put|on\s+station)|\bhold\s+(?:position|station)|\bremain\s+(?:in\s+place|on\s+station)"
                  r"|\bdo\s+not\s+return|\bdon'?t\s+return")
RETURN = re.compile(r"(?<!не\s)(?:вернут|возвра[тщ])|домой|на\s+базу|(?<!not\s)(?<!n't\s)\breturn\s+(?:to\s+base|home)"
                    r"|\bcome\s+(?:back\s+)?home|\bgo\s+home|\brtb\b")

RU_NUM = {"три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
          "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15}
EN_NUM = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
          "thirteen": 13, "fourteen": 14, "fifteen": 15}
WORD_NUM = {**RU_NUM, **EN_NUM}
_NUM = r"\b(\d{1,3}|" + "|".join(WORD_NUM) + r")"
SECONDS = re.compile(_NUM + r"\s*(?:секунд\w*|сек\b|с\b|seconds?\b|secs?\b|s\b)|секунд\w*\s+на\s+" + _NUM)
DWELL_MIN_S, DWELL_MAX_S = 3, 15


def _num(tok):
    return int(tok) if tok.isdigit() else WORD_NUM[tok]


def zones(text: str) -> list:
    """Зоны, названные в приказе, в каноническом порядке; ["all"] для «все квадраты» и при всех шести названных."""
    t = text.lower()
    if ALL_ZONES.search(t):
        return ["all"]
    found = [z for z in ZONES if any(re.search(r"\b" + s, t) for s in ZONE_STEMS[z])]
    return ["all"] if len(found) == len(ZONES) else found


def on_target(text: str):
    t = text.lower()
    for value, pat in (("SKIP", SKIP), ("DWELL_ONLY", DWELL_ONLY), ("DWELL_RECON", DWELL_RECON)):   # «без доразведки» сильнее «доразведки»
        if pat.search(t):
            return value
    return None


def dwell_s(text: str):
    m = SECONDS.search(text.lower())
    if not m:
        return None
    return max(DWELL_MIN_S, min(DWELL_MAX_S, _num(m.group(1) or m.group(2))))


def rtb(text: str):
    t = text.lower()
    stay, ret = bool(STAY.search(t)), bool(RETURN.search(t))
    if stay == ret:              # ничего не сказано или противоречие: решает модель (по умолчанию возврат)
        return None
    return ret


# «D1», «борт 2», «дроны 1, 2 и 3», «drones 1 and 2»: ключевое слово и список номеров
DRONE_REF = re.compile(r"\b(?:d|д|борт\w*|дрон\w*|drones?)\s*[-#№]?\s*(\d{1,2}(?:\s*(?:,|и|and|&)\s*\d{1,2})*)")
# «остальные»: все борта, которых нет ни в одной фразе с номерами
REST = re.compile(r"\bостальн|\bостал\w*\s+борт|\bthe\s+rest\b|\bothers?\b|\bremaining\b")
SEGMENT_END = re.compile(r"[;.!?\n]+|(?=\bостальн|\bостал\w*\s+борт|\bthe\s+rest\b|\bothers?\b|\bremaining\b)")


def drones_named(text: str) -> list:
    nums = {int(n) for m in DRONE_REF.finditer(text.lower()) for n in re.findall(r"\d+", m.group(1))}
    return sorted(nums)


def assignments(text: str, n_drones: int) -> dict:
    """Приказ с адресацией бортов: «D1 и D2 — Дельта и Фокстрот, остальные — Альфа» -> {1: [delta, foxtrot], 2: [...], 3: [alpha], ...}.
    Фраза с номерами бортов и зонами привязывает эти борта к этим зонам; «остальные» с зонами — все борта, не названные по номерам.
    Без адресации (нет ни номеров, ни «остальных» рядом с зонами) -> {}: все борта торгуются за все активные сектора.
    Номер борта вне 1..n_drones — ValueError: молча пропустить адресата хуже, чем отказать."""
    named, rest = {}, []
    for seg in SEGMENT_END.split(text.lower()):
        zs = zones(seg)
        if not seg.strip() or not zs:
            continue
        if REST.match(seg.strip()):
            rest = sorted(set(rest) | set(zs))
        else:
            for d in drones_named(seg):
                if not 1 <= d <= n_drones:
                    raise ValueError(f"борт {d} вне роя 1..{n_drones}")
                named[d] = sorted(set(named.get(d, [])) | set(zs))
    if rest:
        for d in range(1, n_drones + 1):
            named.setdefault(d, list(rest))
    return named


def parse(text: str) -> dict:
    """Поля, которые однозначно следуют из текста; None — в тексте ничего нет, поле берётся из ответа модели."""
    return {"zones": zones(text) or None, "on_target": on_target(text), "dwell_s": dwell_s(text), "rtb_on_complete": rtb(text)}


def merge(rules: dict, llm: dict) -> tuple:
    """-> (поля манифеста, источник каждого поля "rules"|"llm"). Зоны — только из правил (пост-проверка: зон, которых нет в тексте, нет).
    rtb_on_complete=False (борт остаётся в воздухе и не возвращается) модель выставить не может: только правила по явным словам.
    Замер 2026-10-27: Qwen2.5-1.5B ставила False на приказах вроде «Загляни в Дельту»; ответ модели False отбрасывается, поле берётся по умолчанию."""
    out, src = {}, {}
    for k in ("zones", "on_target", "dwell_s", "rtb_on_complete"):
        if rules.get(k) is not None:
            out[k], src[k] = rules[k], "rules"
        elif k != "zones" and llm.get(k) is not None and not (k == "rtb_on_complete" and llm[k] is False):
            out[k], src[k] = llm[k], "llm"
    return out, src
