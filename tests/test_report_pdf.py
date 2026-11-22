"""Tests for the PDF of the report (stage 9), without Home Assistant."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pypdf import PdfReader

from custom_components.ev_charge_log.models import Fee, Session, Tariff
from custom_components.ev_charge_log.report_pdf import (
    EN_DASH,
    REPORT_TEXT_KEYS,
    PdfFormat,
    ReportContext,
    report_filename,
    report_pdf,
)
from custom_components.ev_charge_log.reports import report

BERLIN = ZoneInfo("Europe/Berlin")
TODAY = date(2026, 11, 22)
TRANSLATIONS = (
    Path(__file__).parent.parent / "custom_components/ev_charge_log/translations"
)
TARIFFS = {
    "enbw": Tariff(id="enbw", name="EnBW", price_ac=0.59, price_dc=0.69),
    "free": Tariff(id="free", name="Arbeitgeber", price_ac=0.0),
}
VEHICLES = {"seal": "Seal U", "dolphin": "Dolphin"}
MONTHS_DE = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
GERMAN = PdfFormat(decimal=",", group=".", months=MONTHS_DE, currency="€")


def _texts(language: str) -> dict[str, str]:
    data = json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))
    return {key: value["message"] for key, value in data["exceptions"].items()}


def _day(session: Session) -> date:
    return session.timestamp.astimezone(BERLIN).date()


def _session(session_id: str, ts: datetime, **kwargs) -> Session:
    defaults = {
        "vehicle_id": "seal",
        "energy_kwh": 10.0,
        "charge_type": "ac",
        "cost": 5.9,
        "cost_overridden": False,
        "tariff_id": "enbw",
        "price_per_kwh": 0.59,
    }
    return Session(id=session_id, timestamp=ts, **{**defaults, **kwargs})


def _sessions(count: int) -> list[Session]:
    start = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    return [
        _session(
            str(i),
            start + timedelta(days=3 * i),
            vehicle_id=("seal", "dolphin")[i % 2],
            energy_kwh=1000.5 if i == 0 else 10.0,
            soc_start=20,
            soc_end=80,
            note="Łódź Январь" if i == 1 else None,
        )
        for i in range(count)
    ]


def _context(**kwargs) -> ReportContext:
    defaults = {
        "currency": "EUR",
        "created": TODAY,
        "tz": BERLIN,
        "vehicle_names": VEHICLES,
        "tariff_names": {k: t.name for k, t in TARIFFS.items()},
    }
    return ReportContext(**{**defaults, **kwargs})


def _pdf(
    sessions: list[Session],
    period: str = "2026",
    fmt: PdfFormat = GERMAN,
    language: str = "de",
    context: ReportContext | None = None,
    **filters,
) -> tuple[bytes, list[str]]:
    result = report(
        sessions,
        [Fee("enbw", "seal", "2026-01", 4.99)],
        TARIFFS,
        VEHICLES,
        period,
        _day,
        TODAY,
        **filters,
    )
    data = report_pdf(result, _texts(language), fmt, context or _context())
    pages = [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]
    return data, pages


def test_pdf_with_unicode_and_page_breaks() -> None:
    data, pages = _pdf(_sessions(90), include_sessions=True)
    assert data.startswith(b"%PDF")
    assert len(pages) > 1
    text = "\n".join(pages)
    assert "Ladebericht 2026" in text
    assert "Alle Fahrzeuge" in text
    assert "Łódź Январь" in text  # note with Polish and Cyrillic letters
    assert f"20 {EN_DASH} 80" in text  # SoC range
    assert f"Seite 1 von {len(pages)}" in pages[0]
    assert f"Seite {len(pages)} von {len(pages)}" in pages[-1]
    # Table head repeated on the next page.
    assert pages[-1].count("Ladetarif") >= 1


def test_number_format_of_the_page() -> None:
    _, pages = _pdf(_sessions(3))
    text = "\n".join(pages)
    assert "1.020,5 kWh" in text  # group "." and decimal ","
    assert "€" in text
    _, pages = _pdf(_sessions(3), fmt=PdfFormat(), language="en")
    text = "\n".join(pages)
    assert "1020.5 kWh" in text  # default: no group mark
    assert "EUR" in text  # currency code without symbol


def test_monthly_report_title_with_month_name() -> None:
    _, pages = _pdf(_sessions(3), period="2026-01")
    assert "Ladebericht Januar 2026" in pages[0]
    assert "Pro Tag" in pages[0]


def test_filters_in_the_head_and_note_without_fees() -> None:
    _, pages = _pdf(
        _sessions(3),
        context=_context(vehicle_name="Seal U", tariff_name="EnBW", note=" Łódź "),
        vehicle_id="seal",
        tariff_id="enbw",
        note="Łódź",
    )
    text = pages[0]
    assert "Seal U" in text
    assert "Alle Fahrzeuge" not in text
    assert "Tarif: EnBW" in text
    assert "Notiz enthält: Łódź" in text
    assert "ohne Grundgebühren" in text


def test_empty_report() -> None:
    _, pages = _pdf([], period="2026-02")
    assert len(pages) == 1
    assert "Keine Ladungen und Grundgebühren in diesem Zeitraum." in pages[0]


def test_sessions_only_when_listed() -> None:
    _, pages = _pdf(_sessions(3))
    assert "Uhrzeit" not in "\n".join(pages)
    _, pages = _pdf(_sessions(3), include_sessions=True)
    assert "Uhrzeit" in "\n".join(pages)


@pytest.mark.parametrize(
    ("period", "vehicle", "expected"),
    [
        ("2026", None, "ev-charge-log-2026.pdf"),
        ("2026-03", "BYD Seal U", "ev-charge-log-2026-03-byd-seal-u.pdf"),
        ("2026", "Dolphin (Ehefrau)", "ev-charge-log-2026-dolphin-ehefrau.pdf"),
        ("2026", "Январь", "ev-charge-log-2026.pdf"),  # nothing left for a slug
    ],
)
def test_report_filename(period: str, vehicle: str | None, expected: str) -> None:
    assert report_filename(period, vehicle) == expected


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_report_text_is_translated(language: str) -> None:
    texts = _texts(language)
    assert [key for key in REPORT_TEXT_KEYS if key not in texts] == []
