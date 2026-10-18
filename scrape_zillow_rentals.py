#!/usr/bin/env python3
"""
Zillow Nationwide & State Rentals Scraper -> NoCodeBackend (NCB) Database (`43023_ziplow_db`)

Scrapes comprehensive rental listing data across Colorado or all 50 US states + DC from:
  - https://www.zillow.com/<state>/rentals/ (and paginated /2_p/, /3_p/, ...)
  - https://www.zillow.com/<state>/rent-houses/
  - Individual Zillow `/homedetails/...` and `/apartments/...` pages

Captures 80+ structured columns per listing into NoCodeBackend (`43023_ziplow_db.rentals`):
  - Pricing & Fees: price_monthly, max_price_monthly, base_rent, required_monthly_fee_min/max,
    includes_required_fees, price_per_sqft, rent_zestimate, zestimate, application_fee_cents,
    security_deposit, special_offers, rental_costs_and_fees_json, price_history_json
  - Location & Scores: street_address, city, state, zipcode, county, county_fips, neighborhood,
    timezone, latitude, longitude, walk_score, transit_score, bike_score, neighborhood_details_json
  - Interior & Property: bedrooms, bathrooms, full_bathrooms, half_bathrooms, sqft,
    total_interior_livable_area, year_built, lot_size, parcel_number, tax_annual_amount,
    tax_assessed_value, room_types, flooring, appliances, heating, cooling, laundry,
    parking, pets_allowed, exterior_features, interior_features, amenities_json,
    home_insights, facts_and_features
  - Units, Media & Tours: available_units_count, units_and_floorplans_json, virtual_tour_url,
    floor_plan_url, has_3d_model, image_url, photo_count, photos_json, videos_and_media_json
  - Listing & Contact: home_status, days_on_zillow, time_on_zillow, on_market_date,
    lease_term, agent_name, brokerage_name, contact_phone, provider_listing_id,
    instant_tour_enabled, schools_json, description, scraped_at
"""

import argparse
import datetime
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv
import requests as std_requests

# Load environment variables from .env
load_dotenv()

US_STATES = [
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl",
    "ga", "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me",
    "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh",
    "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
]

DEFAULT_EXAMPLE_URLS = [
    "https://www.zillow.com/homedetails/9499-Sherrelwood-Ln-Highlands-Ranch-CO-80126/13444137_zpid/",
    "https://www.zillow.com/apartments/brighton-co/the-lanterns-at-brighton-crossing/Cq9dK4/",
]

NCB_INSTANCE = os.getenv("NCB_INSTANCE", "43023_ziplow_db")
NCB_SECRET_KEY = os.getenv("NCB_SECRET_KEY", "")
NCB_MCP_KEY = os.getenv("NCB_MCP_KEY", "")
NCB_MCP_URL = os.getenv("NCB_MCP_URL", "https://app.nocodebackend.com/api/mcp/sse")
NCB_DATA_API_URL = os.getenv("NCB_DATA_API_URL", "https://app.nocodebackend.com/api/data")
NCB_TABLE = os.getenv("NCB_TABLE", "rentals")


def parse_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, (int, float)):
        return int(val)
    s = re.sub(r"[^\d]", "", str(val))
    return int(s) if s else None


def parse_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    m = re.search(r"\d+(?:\.\d+)?", str(val).replace(",", ""))
    return float(m.group(0)) if m else None


def join_list(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, list):
        return ", ".join(str(x) for x in val if x is not None and str(x).strip())
    return str(val).strip()


def extract_insight_phrases(home_insights: Any) -> str:
    """Extracts AI insight tags/phrases from Zillow homeInsights structures."""
    if not home_insights:
        return ""
    phrases: List[str] = []
    items = home_insights if isinstance(home_insights, list) else [home_insights]
    for item in items:
        if isinstance(item, dict):
            for ins in item.get("insights") or []:
                if isinstance(ins, dict):
                    for p in ins.get("phrases") or []:
                        if p and p not in phrases:
                            phrases.append(str(p))
    return ", ".join(phrases)


def format_interior_summary(
    bedrooms: Any,
    bathrooms: Any,
    full_bathrooms: Any,
    room_types: str,
    flooring: str,
    total_livable_area: str,
    virtual_tour_url: str,
    extra_features: Optional[Dict[str, Any]] = None,
) -> str:
    """Formats the Facts & Features -> Interior section matching Zillow's layout."""
    lines = ["Facts & features", "Interior", "Bedrooms & bathrooms"]
    if bedrooms is not None:
        lines.append(f"Bedrooms: {bedrooms}")
    if bathrooms is not None:
        lines.append(f"Bathrooms: {bathrooms}")
    if full_bathrooms is not None:
        lines.append(f"Full bathrooms: {full_bathrooms}")

    if room_types:
        lines.extend(["Rooms", f"Room types: {room_types}"])

    if flooring or (extra_features and any(extra_features.values())):
        lines.append("Features")
        if flooring:
            lines.append(f"Flooring: {flooring}")
        if extra_features:
            for k, v in extra_features.items():
                if v:
                    lines.append(f"{k}: {v}")

    if total_livable_area:
        lines.extend(["Interior area", f"Total interior livable area: {total_livable_area}"])

    if virtual_tour_url:
        lines.extend(["Video & virtual tour", f"View virtual tour: {virtual_tour_url}"])

    return "\n".join(lines)


class ZillowColoradoScraper:
    """Scrapes Zillow state/nationwide rental search pages and property/building detail pages."""

    def __init__(self, delay_seconds: float = 1.0):
        self.session = cffi_requests.Session(impersonate="chrome")
        self.delay_seconds = delay_seconds

    def fetch_next_data(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetches a Zillow page and parses its embedded __NEXT_DATA__ JSON payload."""
        try:
            resp = self.session.get(url, timeout=25)
            if resp.status_code != 200:
                print(f"  [!] HTTP {resp.status_code} fetching {url}")
                return None
            match = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                resp.text,
                re.S,
            )
            if not match:
                soup = BeautifulSoup(resp.text, "html.parser")
                script_tag = soup.find("script", id="__NEXT_DATA__")
                if not script_tag or not script_tag.string:
                    print(f"  [!] No __NEXT_DATA__ found on {url}")
                    return None
                return json.loads(script_tag.string)
            return json.loads(match.group(1))
        except Exception as exc:
            print(f"  [!] Error fetching {url}: {exc}")
            return None

    def extract_search_results(self, search_url: str) -> List[Dict[str, Any]]:
        """Extracts listing cards from a Zillow rentals search results page."""
        print(f"🔍 Scraping search page: {search_url}")
        next_data = self.fetch_next_data(search_url)
        if not next_data:
            return []

        search_state = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("searchPageState", {})
        )
        cat1 = search_state.get("cat1", {})
        search_results = cat1.get("searchResults", {})
        list_results = search_results.get("listResults", []) or []
        relaxed_results = search_results.get("relaxedResults", []) or []

        combined = list_results + relaxed_results
        print(f"   Found {len(combined)} rental listings on {search_url}")
        return combined

    def parse_search_card_fallback(self, card: Dict[str, Any], default_state: str = "CO") -> Dict[str, Any]:
        """Extracts all available fields from a Zillow search result card."""
        detail_url = card.get("detailUrl", "")
        if detail_url and detail_url.startswith("/"):
            detail_url = urljoin("https://www.zillow.com", detail_url)

        lat_long = card.get("latLong") or {}
        units = card.get("units") or []
        hdp = (card.get("hdpData") or {}).get("homeInfo") or {}
        ff = card.get("factsAndFeatures") or {}
        recs = card.get("listCardRecommendation") or {}
        flex_recs = recs.get("flexFieldRecommendations") or []
        cta_recs = recs.get("ctaRecommendations") or []

        price_str = card.get("price") or ""
        beds = card.get("beds") if card.get("beds") is not None else hdp.get("bedrooms")
        baths = card.get("baths") if card.get("baths") is not None else hdp.get("bathrooms")
        sqft = card.get("area") if card.get("area") is not None else hdp.get("livingArea")

        unit_prices: List[int] = []
        unit_beds: List[int] = []
        for u in units:
            if isinstance(u, dict):
                up = parse_int(u.get("price"))
                ub = parse_int(u.get("beds"))
                if up:
                    unit_prices.append(up)
                if ub is not None:
                    unit_beds.append(ub)

        if not price_str and units:
            if unit_prices and min(unit_prices) != max(unit_prices):
                price_str = f"${min(unit_prices):,} - ${max(unit_prices):,}/mo"
            elif units[0].get("price"):
                price_str = f"{units[0]['price']}/mo"
        if beds is None and unit_beds:
            beds = min(unit_beds)

        price_monthly = (
            parse_int(card.get("unformattedPrice"))
            or (min(unit_prices) if unit_prices else None)
            or parse_int(card.get("minBaseRent"))
            or parse_int(hdp.get("price"))
            or parse_int(price_str)
        )
        max_price_monthly = (
            (max(unit_prices) if unit_prices else None)
            or parse_int(card.get("maxBaseRent"))
            or price_monthly
        )
        if not price_str and price_monthly:
            price_str = f"${price_monthly:,}/mo"

        base_rent = parse_int(card.get("baseRent") or card.get("minBaseRent")) or price_monthly
        req_fee_min = parse_int(card.get("totalRequiredMonthlyMinFee"))
        req_fee_max = parse_int(card.get("totalRequiredMonthlyMaxFee"))
        includes_fees = bool(card.get("listPriceIncludesRequiredMonthlyFees", False))
        price_note = (
            "Total monthly price - This listing now includes required monthly fees in the total monthly price."
            if includes_fees
            else "Monthly rent"
        )

        is_building = bool(card.get("isBuilding") or "/apartments/" in detail_url or "/b/" in detail_url)
        prop_type = (
            "Apartment / Building"
            if is_building
            else str(hdp.get("homeType") or "Single Family / Home")
        )

        title = card.get("buildingName") or card.get("statusText") or card.get("address") or ""
        sqft_int = parse_int(sqft)
        total_area_str = f"{sqft_int:,} sqft" if sqft_int else ""
        price_per_sqft = (
            round(price_monthly / sqft_int, 2)
            if (price_monthly and sqft_int and sqft_int > 0)
            else None
        )

        # Extract flex recommendations (special offers, time on Zillow, available units)
        special_offers_list: List[str] = []
        time_on_zillow_str = ""
        avail_units = parse_int(card.get("availabilityCount"))
        for fr in flex_recs:
            if isinstance(fr, dict):
                ctype = fr.get("contentType", "")
                dstr = fr.get("displayString", "")
                if ctype == "frSpecialOffer" and dstr:
                    special_offers_list.append(dstr)
                elif ctype == "timeOnInfo" and dstr:
                    time_on_zillow_str = dstr
                elif ctype == "frUnitsAvailable" and avail_units is None:
                    avail_units = parse_int(dstr)

        contact_phone = ""
        for cta in cta_recs:
            if isinstance(cta, dict) and cta.get("contentType") == "PHONE":
                contact_phone = str(cta.get("displayString") or "")
                break

        photos: List[str] = []
        for p in (card.get("carouselPhotosComposable") or {}).get("photoData") or []:
            if isinstance(p, dict) and p.get("photoKey"):
                photos.append(
                    f"https://photos.zillowstatic.com/fp/{p['photoKey']}-p_e.jpg"
                )
        if not photos and card.get("imgSrc"):
            photos.append(card["imgSrc"])

        full_baths = parse_int(ff.get("fullBathroomCount")) or parse_int(baths)
        half_baths = parse_int(ff.get("halfBathroomCount"))
        cooling = "Air Conditioning" if ff.get("hasAirConditioning") else ""
        ext_feats = [
            label
            for key, label in [
                ("hasPool", "Pool"),
                ("hasSpa", "Spa / Hot Tub"),
                ("hasFireplace", "Fireplace"),
            ]
            if ff.get(key)
        ]

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "zpid": str(card.get("zpid") or card.get("id") or hdp.get("zpid") or ""),
            "listing_url": detail_url,
            "property_type": prop_type[:100],
            "title": str(title)[:255],
            "address": str(card.get("address") or "")[:255],
            "street_address": str(card.get("addressStreet") or hdp.get("streetAddress") or "")[:255],
            "city": str(card.get("addressCity") or hdp.get("city") or "")[:100],
            "state": str(card.get("addressState") or hdp.get("state") or default_state.upper())[:50],
            "zipcode": str(card.get("addressZipcode") or hdp.get("zipcode") or "")[:20],
            "price_display": str(price_str)[:100],
            "price_monthly": price_monthly,
            "max_price_monthly": max_price_monthly,
            "base_rent": base_rent,
            "required_monthly_fee_min": req_fee_min,
            "required_monthly_fee_max": req_fee_max,
            "includes_required_fees": 1 if includes_fees else 0,
            "price_per_sqft": price_per_sqft,
            "rent_zestimate": parse_int(hdp.get("rentZestimate")),
            "zestimate": parse_int(card.get("zestimate") or hdp.get("zestimate")),
            "application_fee_cents": None,
            "security_deposit": "",
            "special_offers": "; ".join(special_offers_list),
            "rental_costs_and_fees_json": json.dumps(
                {
                    "baseRent": base_rent,
                    "minBaseRent": card.get("minBaseRent"),
                    "maxBaseRent": card.get("maxBaseRent"),
                    "totalRequiredMonthlyMinFee": req_fee_min,
                    "totalRequiredMonthlyMaxFee": req_fee_max,
                    "listPriceIncludesRequiredMonthlyFees": includes_fees,
                }
            ),
            "price_history_json": "[]",
            "price_note": price_note[:255],
            "county": "",
            "county_fips": "",
            "neighborhood": "",
            "timezone": "",
            "walk_score": None,
            "transit_score": None,
            "bike_score": None,
            "neighborhood_details_json": "{}",
            "bedrooms": parse_int(beds),
            "bathrooms": parse_float(baths),
            "full_bathrooms": full_baths,
            "half_bathrooms": half_baths,
            "sqft": sqft_int,
            "total_interior_livable_area": total_area_str[:100],
            "year_built": None,
            "lot_size": "",
            "parcel_number": "",
            "tax_annual_amount": None,
            "tax_assessed_value": parse_int(hdp.get("taxAssessedValue")),
            "room_types": "",
            "flooring": "",
            "appliances": "",
            "heating": "",
            "cooling": cooling,
            "laundry": "",
            "parking": "",
            "pets_allowed": "",
            "exterior_features": ", ".join(ext_feats),
            "interior_features": format_interior_summary(
                bedrooms=parse_int(beds),
                bathrooms=parse_float(baths),
                full_bathrooms=full_baths,
                room_types="",
                flooring="",
                total_livable_area=total_area_str,
                virtual_tour_url="",
                extra_features={"Cooling": cooling} if cooling else None,
            ),
            "amenities_json": json.dumps({"factsAndFeatures": ff}),
            "home_insights": "",
            "facts_and_features": json.dumps(
                {
                    "units": units,
                    "hdpData": hdp,
                    "factsAndFeatures": ff,
                    "listCardRecommendation": recs,
                    "marketingTreatments": card.get("marketingTreatments"),
                    "rawCardStatus": card.get("statusText"),
                }
            ),
            "available_units_count": avail_units,
            "units_and_floorplans_json": json.dumps(units),
            "virtual_tour_url": "",
            "floor_plan_url": "",
            "has_3d_model": 1 if card.get("has3DModel") else 0,
            "image_url": str(card.get("imgSrc") or "")[:500],
            "photo_count": len(photos),
            "photos_json": json.dumps(photos),
            "videos_and_media_json": json.dumps({"has3DModel": card.get("has3DModel")}),
            "latitude": str(lat_long.get("latitude") or hdp.get("latitude") or "")[:50],
            "longitude": str(lat_long.get("longitude") or hdp.get("longitude") or "")[:50],
            "home_status": str(card.get("statusType") or hdp.get("homeStatus") or "FOR_RENT")[:50],
            "days_on_zillow": parse_int(hdp.get("daysOnZillow")),
            "time_on_zillow": time_on_zillow_str[:100],
            "on_market_date": "",
            "lease_term": "",
            "agent_name": "",
            "brokerage_name": "",
            "contact_phone": contact_phone[:100],
            "provider_listing_id": str(card.get("providerListingId") or "")[:100],
            "instant_tour_enabled": 1 if card.get("isInstantTourEnabled") else 0,
            "schools_json": "[]",
            "description": "",
            "scraped_at": now_str,
        }

    def parse_homedetails_page(
        self, url: str, next_data: Dict[str, Any], base_record: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Extracts all 80+ property, financial, location, media, and interior fields from /homedetails/."""
        base = base_record or {}
        comp_props = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("componentProps", {})
        )
        raw_cache = comp_props.get("gdpClientCache")
        if not raw_cache:
            return base_record

        cache = json.loads(raw_cache) if isinstance(raw_cache, str) else raw_cache
        prop = None
        for _, val in cache.items():
            if isinstance(val, dict) and "property" in val:
                prop = val["property"]
                break

        if not prop:
            return base_record

        reso = prop.get("resoFacts") or {}
        addr_obj = prop.get("address") or {}
        attr_info = prop.get("attributionInfo") or {}
        posting_contact = prop.get("postingContact") or {}
        app_price_detail = prop.get("rentalApplicationPriceDetail") or {}
        rich_media = prop.get("richMedia") or {}

        street = addr_obj.get("streetAddress") or prop.get("streetAddress") or base.get("street_address") or ""
        city = addr_obj.get("city") or prop.get("city") or base.get("city") or ""
        state = addr_obj.get("state") or prop.get("state") or base.get("state") or "CO"
        zipcode = addr_obj.get("zipcode") or prop.get("zipcode") or base.get("zipcode") or ""
        full_address = ", ".join(p for p in [street, city, f"{state} {zipcode}".strip()] if p)

        price_val = parse_int(prop.get("price")) or base.get("price_monthly")
        price_display = f"${price_val:,}/mo" if price_val else (base.get("price_display") or "")
        base_rent = parse_int(prop.get("baseRent")) or price_val
        req_fee_min = parse_int(prop.get("totalRequiredMonthlyMinFee"))
        req_fee_max = parse_int(prop.get("totalRequiredMonthlyMaxFee"))
        includes_fees = bool(prop.get("listPriceIncludesRequiredMonthlyFees", False))
        price_note = (
            "Total monthly price - This listing now includes required monthly fees in the total monthly price."
            if includes_fees
            else "Monthly rent"
        )

        bedrooms = parse_int(reso.get("bedrooms") if reso.get("bedrooms") is not None else prop.get("bedrooms"))
        bathrooms = parse_float(reso.get("bathrooms") if reso.get("bathrooms") is not None else prop.get("bathrooms"))
        full_bathrooms = parse_int(reso.get("bathroomsFull"))
        if full_bathrooms is None and bathrooms is not None:
            full_bathrooms = int(bathrooms)
        half_bathrooms = parse_int(reso.get("bathroomsHalf"))

        sqft_val = parse_int(prop.get("livingArea") or prop.get("livingAreaValue") or reso.get("livingArea"))
        total_livable_area = reso.get("livingArea") or (f"{sqft_val:,} sqft" if sqft_val else "")
        price_per_sqft = parse_float(reso.get("pricePerSquareFoot")) or (
            round(price_val / sqft_val, 2) if (price_val and sqft_val and sqft_val > 0) else None
        )

        room_types = join_list(reso.get("roomTypes") or reso.get("rooms"))
        flooring = join_list(reso.get("flooring"))
        appliances = join_list(reso.get("appliances"))
        cooling = join_list(reso.get("cooling"))
        heating = join_list(reso.get("heating"))
        laundry = join_list(reso.get("laundryFeatures"))
        parking = join_list(reso.get("parkingFeatures") or reso.get("otherParking"))
        pets = join_list(reso.get("allowedPets"))

        # Check atAGlanceFacts for additional fallback values (Cooling, Heating, Pets, Parking, Laundry, Deposit)
        security_deposit = str(reso.get("additionalFeeInfo") or "")
        for glance in reso.get("atAGlanceFacts") or []:
            if isinstance(glance, dict) and glance.get("factValue"):
                label = str(glance.get("factLabel") or "")
                val = str(glance.get("factValue"))
                if label == "Cooling" and not cooling:
                    cooling = val
                elif label == "Heating" and not heating:
                    heating = val
                elif label == "Pets" and not pets:
                    pets = val
                elif label == "Parking" and not parking:
                    parking = val
                elif label == "Laundry" and not laundry:
                    laundry = val
                elif label in ("Deposit & fees", "One-time fees") and not security_deposit:
                    security_deposit = val

        ext_parts: List[str] = []
        for ext_val in [
            reso.get("fencing"),
            join_list(reso.get("patioAndPorchFeatures")),
            join_list(reso.get("exteriorFeatures")),
            join_list(reso.get("poolFeatures")),
            join_list(reso.get("spaFeatures")),
            join_list(reso.get("otherStructures")),
        ]:
            if ext_val:
                ext_parts.append(str(ext_val))
        exterior_features = "; ".join(ext_parts)

        # Virtual tour & floor plan URLs
        virtual_tour_url = (
            prop.get("virtualTourUrl")
            or reso.get("virtualTour")
            or (prop.get("thirdPartyVirtualTour") or {}).get("lightboxUrl")
            or (prop.get("thirdPartyVirtualTour") or {}).get("externalUrl")
            or ""
        )
        if not virtual_tour_url and reso.get("media"):
            for m_item in reso["media"]:
                if isinstance(m_item, dict) and m_item.get("mediaURL"):
                    virtual_tour_url = m_item["mediaURL"]
                    break

        floor_plan_list = rich_media.get("floorPlan") or []
        floor_plan_url = (
            floor_plan_list[0].get("viewerUrl", "")
            if (floor_plan_list and isinstance(floor_plan_list[0], dict))
            else ""
        )

        interior_text = format_interior_summary(
            bedrooms=bedrooms,
            bathrooms=int(bathrooms) if bathrooms and bathrooms.is_integer() else bathrooms,
            full_bathrooms=full_bathrooms,
            room_types=room_types,
            flooring=flooring,
            total_livable_area=total_livable_area,
            virtual_tour_url=virtual_tour_url,
            extra_features={
                "Appliances": appliances,
                "Heating": heating,
                "Cooling": cooling,
                "Laundry": laundry,
                "Parking": parking,
                "Pets": pets,
            },
        )

        clean_reso = {
            k: v
            for k, v in reso.items()
            if v not in (None, [], {}, False, "")
        }

        photos: List[str] = []
        for p in prop.get("responsivePhotos") or []:
            if isinstance(p, dict) and p.get("url"):
                photos.append(p["url"])
        if not photos:
            for p in prop.get("responsivePhotosOriginalRatio") or []:
                if isinstance(p, dict):
                    jpegs = (p.get("mixedSources") or {}).get("jpeg") or []
                    if jpegs and isinstance(jpegs[-1], dict) and jpegs[-1].get("url"):
                        photos.append(jpegs[-1]["url"])
        image_url = photos[0] if photos else base.get("image_url", "")

        prop_type_list = reso.get("propertySubType") or []
        prop_type = (
            prop_type_list[0]
            if prop_type_list
            else ( prop.get("propertyTypeDimension") or reso.get("homeType") or prop.get("homeType") or "Single Family Residence")
        )

        lot_area_val = prop.get("lotAreaValue") or reso.get("lotAreaValue")
        lot_area_units = prop.get("lotAreaUnits") or reso.get("lotAreaUnits") or "sqft"
        lot_size_str = f"{lot_area_val} {lot_area_units}".strip() if lot_area_val else str(reso.get("lotSize") or "")

        neighborhood = (
            addr_obj.get("neighborhood")
            or addr_obj.get("subdivision")
            or addr_obj.get("community")
            or reso.get("subdivisionName")
            or ""
        )
        home_insights_str = extract_insight_phrases(prop.get("homeInsights"))
        agent_name = attr_info.get("agentName") or posting_contact.get("name") or ""
        brokerage_name = prop.get("brokerageName") or attr_info.get("brokerName") or attr_info.get("mlsName") or ""
        contact_phone = attr_info.get("agentPhoneNumber") or attr_info.get("brokerPhoneNumber") or base.get("contact_phone") or ""

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "zpid": str(prop.get("zpid") or comp_props.get("zpid") or base.get("zpid") or ""),
            "listing_url": url,
            "property_type": str(prop_type)[:100],
            "title": full_address[:255],
            "address": full_address[:255],
            "street_address": str(street)[:255],
            "city": str(city)[:100],
            "state": str(state)[:50],
            "zipcode": str(zipcode)[:20],
            "price_display": price_display[:100],
            "price_monthly": price_val,
            "max_price_monthly": price_val,
            "base_rent": base_rent,
            "required_monthly_fee_min": req_fee_min,
            "required_monthly_fee_max": req_fee_max,
            "includes_required_fees": 1 if includes_fees else 0,
            "price_per_sqft": price_per_sqft,
            "rent_zestimate": parse_int(prop.get("rentZestimate")),
            "zestimate": parse_int(prop.get("zestimate")),
            "application_fee_cents": parse_int(app_price_detail.get("actualPriceInCents")),
            "security_deposit": security_deposit[:255],
            "special_offers": base.get("special_offers") or "",
            "rental_costs_and_fees_json": json.dumps(prop.get("rentalCostsAndFees") or {}),
            "price_history_json": json.dumps(prop.get("priceHistory") or []),
            "price_note": price_note[:255],
            "county": str(prop.get("county") or "")[:100],
            "county_fips": str(prop.get("countyFIPS") or "")[:20],
            "neighborhood": str(neighborhood)[:150],
            "timezone": str(prop.get("timeZone") or "")[:100],
            "walk_score": None,
            "transit_score": None,
            "bike_score": None,
            "neighborhood_details_json": json.dumps(
                {
                    "nearbyCities": prop.get("nearbyCities"),
                    "nearbyZipcodes": prop.get("nearbyZipcodes"),
                    "nearbyHomes": prop.get("nearbyHomes"),
                    "nearbyBuildings": prop.get("nearbyBuildings"),
                    "marketTrendsURL": prop.get("marketTrendsURL"),
                    "rentZestimateURL": prop.get("rentZestimateURL"),
                }
            ),
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "full_bathrooms": full_bathrooms,
            "half_bathrooms": half_bathrooms,
            "sqft": sqft_val,
            "total_interior_livable_area": str(total_livable_area)[:100],
            "year_built": parse_int(prop.get("yearBuilt") or reso.get("yearBuilt")),
            "lot_size": lot_size_str[:100],
            "parcel_number": str(reso.get("parcelNumber") or prop.get("parcelId") or "")[:100],
            "tax_annual_amount": parse_int(reso.get("taxAnnualAmount")),
            "tax_assessed_value": parse_int(reso.get("taxAssessedValue")),
            "room_types": room_types,
            "flooring": flooring[:255],
            "appliances": appliances,
            "heating": heating[:255],
            "cooling": cooling[:255],
            "laundry": laundry[:255],
            "parking": parking[:255],
            "pets_allowed": pets[:255],
            "exterior_features": exterior_features,
            "interior_features": interior_text,
            "amenities_json": json.dumps(
                {
                    "associationAmenities": reso.get("associationAmenities"),
                    "communityFeatures": reso.get("communityFeatures"),
                    "buildingFeatures": reso.get("buildingFeatures"),
                    "interiorFeatures": reso.get("interiorFeatures"),
                    "accessibilityFeatures": reso.get("accessibilityFeatures"),
                    "atAGlanceFacts": reso.get("atAGlanceFacts"),
                }
            ),
            "home_insights": home_insights_str,
            "facts_and_features": json.dumps(
                {
                    "interior": {
                        "bedrooms": bedrooms,
                        "bathrooms": bathrooms,
                        "full_bathrooms": full_bathrooms,
                        "half_bathrooms": half_bathrooms,
                        "room_types": room_types,
                        "flooring": flooring,
                        "appliances": appliances,
                        "heating": heating,
                        "cooling": cooling,
                        "laundry": laundry,
                        "total_interior_livable_area": total_livable_area,
                    },
                    "property": {
                        "parking": parking,
                        "pets_allowed": pets,
                        "exterior_features": exterior_features,
                        "year_built": prop.get("yearBuilt"),
                        "lot_size": lot_size_str,
                        "parcel_number": reso.get("parcelNumber") or prop.get("parcelId"),
                        "last_sold_price": prop.get("lastSoldPrice"),
                    },
                    "financial": {
                        "price": price_val,
                        "base_rent": base_rent,
                        "required_monthly_fee_min": req_fee_min,
                        "required_monthly_fee_max": req_fee_max,
                        "rent_zestimate": prop.get("rentZestimate"),
                        "zestimate": prop.get("zestimate"),
                        "tax_annual_amount": reso.get("taxAnnualAmount"),
                        "tax_assessed_value": reso.get("taxAssessedValue"),
                    },
                    "virtual_tour_url": virtual_tour_url,
                    "floor_plan_url": floor_plan_url,
                    "resoFacts": clean_reso,
                }
            ),
            "available_units_count": 1,
            "units_and_floorplans_json": json.dumps(
                [
                    {
                        "zpid": prop.get("zpid"),
                        "beds": bedrooms,
                        "baths": bathrooms,
                        "sqft": sqft_val,
                        "price": price_val,
                        "baseRent": base_rent,
                    }
                ]
            ),
            "virtual_tour_url": str(virtual_tour_url)[:500],
            "floor_plan_url": str(floor_plan_url)[:500],
            "has_3d_model": 1 if (prop.get("vrModel") or virtual_tour_url) else 0,
            "image_url": str(image_url)[:500],
            "photo_count": parse_int(prop.get("photoCount")) or len(photos),
            "photos_json": json.dumps(photos),
            "videos_and_media_json": json.dumps(
                {
                    "virtualTourUrl": virtual_tour_url,
                    "floorPlanUrl": floor_plan_url,
                    "thirdPartyVirtualTour": prop.get("thirdPartyVirtualTour"),
                    "vrModel": prop.get("vrModel"),
                    "richMedia": rich_media,
                    "media": reso.get("media"),
                    "streetView": prop.get("streetViewTileImageUrlMediumAddress"),
                    "staticMap": prop.get("staticMap"),
                }
            ),
            "latitude": str(prop.get("latitude") or base.get("latitude") or "")[:50],
            "longitude": str(prop.get("longitude") or base.get("longitude") or "")[:50],
            "home_status": str(prop.get("homeStatus") or "FOR_RENT")[:50],
            "days_on_zillow": parse_int(prop.get("daysOnZillow")),
            "time_on_zillow": str(prop.get("timeOnZillow") or "")[:100],
            "on_market_date": str(reso.get("onMarketDate") or reso.get("availabilityDate") or "")[:100],
            "lease_term": str(reso.get("leaseTerm") or "")[:255],
            "agent_name": str(agent_name)[:255],
            "brokerage_name": str(brokerage_name)[:255],
            "contact_phone": str(contact_phone)[:100],
            "provider_listing_id": str(prop.get("providerListingID") or base.get("provider_listing_id") or "")[:100],
            "instant_tour_enabled": base.get("instant_tour_enabled", 0),
            "schools_json": json.dumps(prop.get("schools") or []),
            "description": str(prop.get("description") or ""),
            "scraped_at": now_str,
        }

    def parse_apartment_building_page(
        self, url: str, next_data: Dict[str, Any], base_record: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Extracts all 80+ building, floor plan, fee, amenity, score, school, and tour fields from /apartments/."""
        base = base_record or {}
        comp_props = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("componentProps", {})
        )
        building = (
            comp_props.get("initialReduxState", {})
            .get("gdp", {})
            .get("building", {})
        )
        if not building:
            return base_record

        building_name = building.get("buildingName") or base.get("title") or ""
        addr_obj = building.get("address") or {}
        street = building.get("streetAddress") or addr_obj.get("streetAddress") or base.get("street_address") or ""
        city = building.get("city") or addr_obj.get("city") or base.get("city") or ""
        state = building.get("state") or addr_obj.get("state") or base.get("state") or "CO"
        zipcode = building.get("zipcode") or addr_obj.get("zipcode") or base.get("zipcode") or ""
        full_address = building.get("fullAddress") or ", ".join(
            p for p in [street, city, f"{state} {zipcode}".strip()] if p
        )

        floor_plans = building.get("floorPlans") or []
        ungrouped_units = building.get("ungroupedUnits") or []
        all_plans = floor_plans if floor_plans else ungrouped_units

        min_prices: List[int] = []
        max_prices: List[int] = []
        base_rents: List[int] = []
        fee_mins: List[int] = []
        fee_maxs: List[int] = []
        beds_list: List[int] = []
        baths_list: List[float] = []
        sqfts_list: List[int] = []
        plan_summaries: List[Dict[str, Any]] = []

        for fp in all_plans:
            if not isinstance(fp, dict):
                continue
            p_min = parse_int(fp.get("minPrice") or fp.get("price"))
            p_max = parse_int(fp.get("maxPrice") or fp.get("price") or p_min)
            b_rent = parse_int(fp.get("minBaseRent"))
            f_min = parse_int(fp.get("totalRequiredMonthlyMinFee"))
            f_max = parse_int(fp.get("totalRequiredMonthlyMaxFee"))
            b_val = parse_int(fp.get("beds"))
            ba_val = parse_float(fp.get("baths"))
            sq_val = parse_int(fp.get("sqft"))

            if p_min:
                min_prices.append(p_min)
            if p_max:
                max_prices.append(p_max)
            if b_rent:
                base_rents.append(b_rent)
            if f_min is not None:
                fee_mins.append(f_min)
            if f_max is not None:
                fee_maxs.append(f_max)
            if b_val is not None:
                beds_list.append(b_val)
            if ba_val is not None:
                baths_list.append(ba_val)
            if sq_val:
                sqfts_list.append(sq_val)

            units_detail = []
            for u in fp.get("units") or []:
                if isinstance(u, dict):
                    units_detail.append(
                        {
                            "unitNumber": u.get("unitNumber"),
                            "zpid": u.get("zpid"),
                            "price": parse_int(u.get("price")),
                            "beds": parse_int(u.get("beds")),
                            "baths": parse_float(u.get("baths")),
                            "sqft": parse_int(u.get("sqft")),
                            "availableFrom": u.get("availableFrom"),
                        }
                    )

            plan_summaries.append(
                {
                    "name": fp.get("name"),
                    "zpid": fp.get("zpid"),
                    "beds": b_val,
                    "baths": ba_val,
                    "sqft": sq_val,
                    "minPrice": p_min,
                    "maxPrice": p_max,
                    "minBaseRent": b_rent,
                    "requiredMonthlyMinFee": f_min,
                    "requiredMonthlyMaxFee": f_max,
                    "availableFrom": fp.get("availableFrom"),
                    "unitCount": len(units_detail),
                    "units": units_detail,
                }
            )

        price_monthly = min(min_prices) if min_prices else base.get("price_monthly")
        max_price = max(max_prices) if max_prices else (max(min_prices) if min_prices else base.get("max_price_monthly"))
        base_rent = min(base_rents) if base_rents else (base.get("base_rent") or price_monthly)
        req_fee_min = min(fee_mins) if fee_mins else base.get("required_monthly_fee_min")
        req_fee_max = max(fee_maxs) if fee_maxs else base.get("required_monthly_fee_max")

        if price_monthly and max_price and max_price > price_monthly:
            price_display = f"${price_monthly:,} - ${max_price:,}/mo"
        elif price_monthly:
            price_display = f"${price_monthly:,}+/mo"
        else:
            price_display = base.get("price_display") or ""

        includes_fees = bool(building.get("listPriceIncludesRequiredMonthlyFees", False))
        price_note = (
            "Total monthly price - This listing now includes required monthly fees in the total monthly price."
            if includes_fees
            else "Base rent / Floor plan pricing"
        )

        bedrooms = min(beds_list) if beds_list else base.get("bedrooms")
        bathrooms = min(baths_list) if baths_list else base.get("bathrooms")
        full_bathrooms = int(bathrooms) if bathrooms is not None else None
        half_bathrooms = (
            1 if (bathrooms is not None and (bathrooms % 1) >= 0.5) else 0
        ) if bathrooms is not None else None
        sqft_val = min(sqfts_list) if sqfts_list else base.get("sqft")
        if sqfts_list and min(sqfts_list) != max(sqfts_list):
            total_livable_area = f"{min(sqfts_list):,} - {max(sqfts_list):,} sqft"
        elif sqft_val:
            total_livable_area = f"{sqft_val:,} sqft"
        else:
            total_livable_area = ""

        price_per_sqft = (
            round(price_monthly / sqft_val, 2)
            if (price_monthly and sqft_val and sqft_val > 0)
            else None
        )

        # Extract amenities & interior features
        structured_amenities = building.get("structuredAmenities") or {}
        unit_features_groups = ((structured_amenities.get("unitFeatures") or {}).get("amenityGroups")) or []
        building_amenity_groups = ((structured_amenities.get("buildingAmenities") or {}).get("amenityGroups")) or []

        unit_features_map: Dict[str, str] = {}
        for grp in unit_features_groups:
            if isinstance(grp, dict) and grp.get("title"):
                unit_features_map[grp["title"]] = join_list(grp.get("amenities"))

        building_features_map: Dict[str, str] = {}
        for grp in building_amenity_groups:
            if isinstance(grp, dict) and grp.get("title"):
                building_features_map[grp["title"]] = join_list(grp.get("amenities"))

        amenity_details = building.get("amenityDetails") or {}
        amenity_summary = building.get("amenitySummary") or {}
        custom_amenities_obj = amenity_details.get("customAmenities") or {}
        custom_raw = (
            custom_amenities_obj.get("rawAmenities")
            if isinstance(custom_amenities_obj, dict)
            else []
        ) or []

        flooring_items = [
            item
            for item in custom_raw
            if isinstance(item, str)
            and any(w in item.lower() for w in ["floor", "hardwood", "plank", "carpet", "tile", "vinyl"])
        ]
        flooring = join_list(flooring_items) or unit_features_map.get("Flooring", "")
        appliances = (
            unit_features_map.get("Appliances")
            or join_list(building.get("commonUnitAmenities"))
        )
        cooling = unit_features_map.get("Cooling", "")
        heating = unit_features_map.get("Heating", "")
        laundry = (
            join_list(amenity_summary.get("laundry"))
            or unit_features_map.get("Laundry", "")
        )
        parking_items = [
            item
            for item in custom_raw
            if isinstance(item, str) and any(w in item.lower() for w in ["garage", "parking", "carport"])
        ]
        parking = join_list(parking_items) or building_features_map.get("Parking", "")
        pets = join_list(amenity_details.get("pets"))

        # Pet deposit / security deposit info
        pet_policies = (building.get("detailedPetPolicy") or {}).get("petPolicies") or []
        pet_deposits = [
            f"Pet deposit: ${pp['deposit']}"
            for pp in pet_policies
            if isinstance(pp, dict) and pp.get("deposit")
        ]
        security_deposit = "; ".join(pet_deposits)

        exterior_features = "; ".join(
            v
            for v in [
                unit_features_map.get("Other", ""),
                building_features_map.get("Outdoor common areas", ""),
                building_features_map.get("Fitness & sports", ""),
            ]
            if v
        )

        room_types = join_list(
            [fp.get("name") for fp in all_plans if isinstance(fp, dict) and fp.get("name")]
        )

        # Special offers
        offers_list = [
            str(so.get("description") or "").replace("\n", " ").strip()
            for so in (building.get("specialOffers") or [])
            if isinstance(so, dict) and so.get("description")
        ]
        special_offers = " | ".join(offers_list) or base.get("special_offers", "")

        # Virtual tour extraction
        virtual_tour_url = ""
        tp_tours = building.get("thirdPartyVirtualTours") or []
        if tp_tours and isinstance(tp_tours[0], dict):
            virtual_tour_url = (
                tp_tours[0].get("lightboxUrl")
                or tp_tours[0].get("externalUrl")
                or tp_tours[0].get("staticUrl")
                or ""
            )
        if not virtual_tour_url:
            for vr_list_key in ("unitsVRModels", "amenitiesVRModels"):
                vr_list = building.get(vr_list_key) or []
                if vr_list and isinstance(vr_list[0], dict) and vr_list[0].get("vrModelUrl"):
                    virtual_tour_url = vr_list[0]["vrModelUrl"]
                    break

        interior_text = format_interior_summary(
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            full_bathrooms=full_bathrooms,
            room_types=room_types,
            flooring=flooring,
            total_livable_area=total_livable_area,
            virtual_tour_url=virtual_tour_url,
            extra_features=unit_features_map,
        )

        photos: List[str] = []
        for p in building.get("galleryPhotos") or building.get("photos") or []:
            if isinstance(p, dict):
                mixed = (p.get("mixedSources") or {}).get("jpeg") or []
                u = p.get("url") or (mixed[-1].get("url") if mixed and isinstance(mixed[-1], dict) else "")
                if u:
                    photos.append(u)
        image_url = photos[0] if photos else base.get("image_url", "")

        building_key = (
            (comp_props.get("query") or {}).get("buildingKey")
            or building.get("lotId")
            or building.get("zpid")
            or base.get("zpid")
            or ""
        )

        rental_costs = building.get("rentalCostsAndFees") or {}
        lease_duration = rental_costs.get("leaseDuration")
        lease_term = f"{lease_duration} months" if lease_duration else ""
        contact_info = building.get("contactInfo") or {}
        units_summary = building.get("rentalUnitsSummary") or {}
        avail_units = (
            parse_int(units_summary.get("availableUnitCount"))
            or base.get("available_units_count")
            or sum(p.get("unitCount", 0) for p in plan_summaries)
        )

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "zpid": str(building_key),
            "listing_url": url,
            "property_type": str(building.get("communitySubType") or "Apartment / Townhome Community")[:100],
            "title": str(building_name or full_address)[:255],
            "address": str(full_address)[:255],
            "street_address": str(street)[:255],
            "city": str(city)[:100],
            "state": str(state)[:50],
            "zipcode": str(zipcode)[:20],
            "price_display": str(price_display)[:100],
            "price_monthly": price_monthly,
            "max_price_monthly": max_price,
            "base_rent": base_rent,
            "required_monthly_fee_min": req_fee_min,
            "required_monthly_fee_max": req_fee_max,
            "includes_required_fees": 1 if includes_fees else 0,
            "price_per_sqft": price_per_sqft,
            "rent_zestimate": parse_int((building.get("marketTrendsSummary") or {}).get("medianRent")),
            "zestimate": None,
            "application_fee_cents": None,
            "security_deposit": security_deposit[:255],
            "special_offers": special_offers,
            "rental_costs_and_fees_json": json.dumps(rental_costs),
            "price_history_json": "[]",
            "price_note": price_note[:255],
            "county": str(building.get("county") or "")[:100],
            "county_fips": "",
            "neighborhood": str(building.get("neighborhood") or addr_obj.get("neighborhood") or "")[:150],
            "timezone": str(building.get("bestGuessTimezone") or "")[:100],
            "walk_score": parse_int((building.get("walkScore") or {}).get("walkscore")),
            "transit_score": parse_int((building.get("transitScore") or {}).get("transit_score")),
            "bike_score": parse_int((building.get("bikeScore") or {}).get("bikescore")),
            "neighborhood_details_json": json.dumps(
                {
                    "neighborhoodDescription": building.get("neighborhoodDescription"),
                    "neighborhoodHighlights": building.get("neighborhoodHighlights"),
                    "marketTrendsSummary": building.get("marketTrendsSummary"),
                    "walkScore": building.get("walkScore"),
                    "transitScore": building.get("transitScore"),
                    "bikeScore": building.get("bikeScore"),
                    "nearbyNeighborhoods": building.get("nearbyNeighborhoods"),
                    "nearbyCities": building.get("nearbyCities"),
                    "nearbyZipcodes": building.get("nearbyZipcodes"),
                    "pointsOfInterest": building.get("pointsOfInterest"),
                }
            ),
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "full_bathrooms": full_bathrooms,
            "half_bathrooms": half_bathrooms,
            "sqft": sqft_val,
            "total_interior_livable_area": str(total_livable_area)[:100],
            "year_built": None,
            "lot_size": "",
            "parcel_number": str(building.get("lotId") or "")[:100],
            "tax_annual_amount": None,
            "tax_assessed_value": None,
            "room_types": room_types,
            "flooring": str(flooring)[:255],
            "appliances": str(appliances),
            "heating": str(heating)[:255],
            "cooling": str(cooling)[:255],
            "laundry": str(laundry)[:255],
            "parking": str(parking)[:255],
            "pets_allowed": str(pets)[:255],
            "exterior_features": exterior_features,
            "interior_features": interior_text,
            "amenities_json": json.dumps(
                {
                    "structuredAmenities": structured_amenities,
                    "amenityDetails": amenity_details,
                    "amenitySummary": amenity_summary,
                    "commonUnitAmenities": building.get("commonUnitAmenities"),
                    "buildingAttributes": building.get("buildingAttributes"),
                    "detailedPetPolicy": building.get("detailedPetPolicy"),
                    "screeningCriteria": building.get("screeningCriteria"),
                }
            ),
            "home_insights": extract_insight_phrases(building.get("homeInsights")),
            "facts_and_features": json.dumps(
                {
                    "buildingName": building_name,
                    "floorPlans": plan_summaries,
                    "unitFeatures": unit_features_map,
                    "buildingAmenities": building_amenity_groups,
                    "commonUnitAmenities": building.get("commonUnitAmenities"),
                    "customAmenities": custom_raw,
                    "pets": amenity_details.get("pets"),
                    "detailedPetPolicy": building.get("detailedPetPolicy"),
                    "specialOffers": building.get("specialOffers"),
                    "rentalUnitsSummary": units_summary,
                    "virtualTourUrl": virtual_tour_url,
                }
            ),
            "available_units_count": avail_units,
            "units_and_floorplans_json": json.dumps(plan_summaries),
            "virtual_tour_url": str(virtual_tour_url)[:500],
            "floor_plan_url": "",
            "has_3d_model": 1
            if (virtual_tour_url or building.get("unitsVRModels") or building.get("amenitiesVRModels"))
            else 0,
            "image_url": str(image_url)[:500],
            "photo_count": parse_int(building.get("photoCount")) or len(photos),
            "photos_json": json.dumps(photos),
            "videos_and_media_json": json.dumps(
                {
                    "thirdPartyVirtualTours": building.get("thirdPartyVirtualTours"),
                    "unitsVRModels": building.get("unitsVRModels"),
                    "amenitiesVRModels": building.get("amenitiesVRModels"),
                    "videos": building.get("videos"),
                    "engrainInteractiveMap": building.get("engrain"),
                    "staticMap": building.get("staticMap"),
                    "streetViewAddress": building.get("streetViewAddress"),
                }
            ),
            "latitude": str(building.get("latitude") or base.get("latitude") or "")[:50],
            "longitude": str(building.get("longitude") or base.get("longitude") or "")[:50],
            "home_status": str(building.get("buildingType") or "FOR_RENT")[:50],
            "days_on_zillow": None,
            "time_on_zillow": "",
            "on_market_date": "",
            "lease_term": lease_term[:255],
            "agent_name": str(contact_info.get("agentFullName") or "")[:255],
            "brokerage_name": str(building.get("rentalFeedId") or "")[:255],
            "contact_phone": str(building.get("buildingPhoneNumber") or base.get("contact_phone") or "")[:100],
            "provider_listing_id": str(
                contact_info.get("providerListingID") or building.get("rentalFeedId") or base.get("provider_listing_id") or ""
            )[:100],
            "instant_tour_enabled": 1 if building.get("isInstantTourEnabled") else 0,
            "schools_json": json.dumps(building.get("assignedSchools") or []),
            "description": str(building.get("description") or ""),
            "scraped_at": now_str,
        }

    def scrape_detail_url(
        self, url: str, base_record: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Scrapes either a /homedetails/ page or an /apartments/ building page."""
        next_data = self.fetch_next_data(url)
        if not next_data:
            return base_record

        if "/homedetails/" in url:
            return self.parse_homedetails_page(url, next_data, base_record)
        if "/apartments/" in url or "/b/" in url:
            return self.parse_apartment_building_page(url, next_data, base_record)

        # Auto-detect by componentProps structure
        comp_props = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("componentProps", {})
        )
        if "gdpClientCache" in comp_props:
            return self.parse_homedetails_page(url, next_data, base_record)
        if "initialReduxState" in comp_props:
            return self.parse_apartment_building_page(url, next_data, base_record)
        return base_record


class NoCodeBackendClient:
    """Pushes scraped rental records to NoCodeBackend via MCP (`ncb`) and REST Data API."""

    def __init__(
        self,
        instance: str = NCB_INSTANCE,
        secret_key: str = NCB_SECRET_KEY,
        mcp_key: str = NCB_MCP_KEY,
        mcp_url: str = NCB_MCP_URL,
        data_api_url: str = NCB_DATA_API_URL,
        table: str = NCB_TABLE,
    ):
        self.instance = instance
        self.secret_key = secret_key
        self.mcp_key = mcp_key
        self.mcp_url = mcp_url
        self.data_api_url = data_api_url.rstrip("/")
        self.table = table

    def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invokes a NoCodeBackend MCP tool over JSON-RPC HTTP POST."""
        if not self.mcp_key:
            raise RuntimeError("NCB_MCP_KEY is required for MCP tool calls.")
        headers = {
            "Authorization": f"Bearer {self.mcp_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1000000,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        resp = std_requests.post(self.mcp_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_existing_urls(self) -> Dict[str, Any]:
        """Fetches existing rental records by listing_url to prevent duplicates."""
        existing: Dict[str, Any] = {}
        if self.mcp_key:
            try:
                res = self.call_mcp_tool(
                    "execute_sql",
                    {
                        "database": self.instance,
                        "sql": f"SELECT id, listing_url FROM {self.table} LIMIT 10000;",
                    },
                )
                text_items = res.get("result", {}).get("content", [])
                for item in text_items:
                    txt = item.get("text", "")
                    if "listing_url" in txt:
                        parsed = json.loads(txt)
                        for res_block in parsed.get("results") or []:
                            for r in res_block.get("rows") or []:
                                if isinstance(r, dict) and r.get("listing_url"):
                                    existing[r["listing_url"]] = r.get("id")
                if existing:
                    return existing
            except Exception as exc:
                print(f"  [!] Warning checking existing records via MCP: {exc}")

        try:
            if self.secret_key:
                url = f"{self.data_api_url}/read/{self.table}?instance={self.instance}&limit=5000"
                headers = {
                    "Content-Type": "application/json",
                    "X-Database-Instance": self.instance,
                    "Authorization": f"Bearer {self.secret_key}",
                }
                resp = std_requests.get(url, headers=headers, timeout=25)
                if resp.ok:
                    body = resp.json()
                    rows = body.get("data", body) if isinstance(body, dict) else body
                    if isinstance(rows, list):
                        for r in rows:
                            if isinstance(r, dict) and r.get("listing_url"):
                                existing[r["listing_url"]] = r.get("id")
        except Exception as exc:
            print(f"  [!] Warning checking existing records via Data API: {exc}")

        return existing

    def upsert_records(
        self,
        records: List[Dict[str, Any]],
        existing_cache: Optional[Dict[str, Any]] = None,
        update_existing: bool = True,
    ) -> Dict[str, int]:
        """Inserts new records or updates existing ones in NoCodeBackend."""
        existing = existing_cache if existing_cache is not None else self.get_existing_urls()
        to_insert: List[Dict[str, Any]] = []
        updated_count = 0

        for rec in records:
            l_url = rec.get("listing_url", "")
            rec_id = existing.get(l_url)
            if rec_id and isinstance(rec_id, int) and update_existing:
                try:
                    if self.mcp_key:
                        res = self.call_mcp_tool(
                            "query_data_api",
                            {
                                "database": self.instance,
                                "table": self.table,
                                "action": "update",
                                "id": str(rec_id),
                                "secret_key": self.secret_key,
                                "body": rec,
                            },
                        )
                        if not res.get("result", {}).get("isError", False):
                            updated_count += 1
                            continue
                except Exception as exc:
                    print(f"  [!] Update failed for id={rec_id}: {exc}")
            if not rec_id:
                to_insert.append(rec)
                if l_url:
                    existing[l_url] = True

        inserted_count = 0
        if to_insert:
            if self.mcp_key:
                batch_size = 20
                for idx in range(0, len(to_insert), batch_size):
                    batch = to_insert[idx : idx + batch_size]
                    res = self.call_mcp_tool(
                        "insert_mock_data",
                        {
                            "database": self.instance,
                            "table": self.table,
                            "records": batch,
                        },
                    )
                    is_err = res.get("result", {}).get("isError", False)
                    if not is_err:
                        inserted_count += len(batch)
                    else:
                        print(f"  [!] MCP batch insert warning: {res}")
            elif self.secret_key:
                for rec in to_insert:
                    url = f"{self.data_api_url}/create/{self.table}?instance={self.instance}"
                    headers = {
                        "Content-Type": "application/json",
                        "X-Database-Instance": self.instance,
                        "Authorization": f"Bearer {self.secret_key}",
                    }
                    r = std_requests.post(url, headers=headers, json=rec, timeout=20)
                    if r.ok:
                        inserted_count += 1

        return {"inserted": inserted_count, "updated": updated_count}


def build_state_search_urls(
    states: List[str], pages_per_state: int = 1, include_houses: bool = True
) -> List[tuple[str, str]]:
    """Returns a list of (state_code, search_url) tuples across the requested states and pages."""
    urls: List[tuple[str, str]] = []
    for st in states:
        st_clean = st.lower().strip()
        for page in range(1, pages_per_state + 1):
            suffix = f"{page}_p/" if page > 1 else ""
            urls.append((st_clean.upper(), f"https://www.zillow.com/{st_clean}/rentals/{suffix}"))
            if include_houses:
                urls.append((st_clean.upper(), f"https://www.zillow.com/{st_clean}/rent-houses/{suffix}"))
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape Colorado or Nationwide US rentals (80+ fields) from Zillow into NoCodeBackend (NCB)."
    )
    parser.add_argument(
        "--nationwide",
        action="store_true",
        help="Scrape rentals across all 50 US states + DC.",
    )
    parser.add_argument(
        "--states",
        nargs="*",
        default=["co"],
        help="State codes to scrape (e.g. `--states co tx ca ny` or `--states all`). Default: co.",
    )
    parser.add_argument(
        "--pages-per-state",
        type=int,
        default=1,
        help="Number of search pages to fetch per state category (default: 1).",
    )
    parser.add_argument(
        "--per-state",
        type=int,
        default=25,
        help="Maximum listings to collect per state (default: 25).",
    )
    parser.add_argument(
        "--max-listings",
        type=int,
        default=5000,
        help="Maximum total listings to scrape across the run (default: 5000).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: extract all search card + hdpData fields without fetching each detail page.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update already-stored listings with newly enriched 80+ column data.",
    )
    parser.add_argument(
        "--detail-urls",
        nargs="*",
        default=[],
        help="Specific Zillow property/apartment URLs to scrape in full detail.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="Delay in seconds between HTTP requests (default: 0.35).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and print JSON output without writing to NoCodeBackend.",
    )
    args = parser.parse_args()

    if args.nationwide or (len(args.states) == 1 and args.states[0].lower() == "all"):
        target_states = US_STATES
    else:
        target_states = [s.lower() for s in args.states]

    print("============================================================")
    print("🇺🇸  Zillow Rentals Scraper (80+ Fields) -> NoCodeBackend (NCB)")
    print(f"📌 Database Instance: {NCB_INSTANCE} (Table: {NCB_TABLE})")
    print(f"🌎 Target States ({len(target_states)}): {', '.join(s.upper() for s in target_states)}")
    print(f"⚡ Mode: {'FAST (Search Cards + hdpData)' if args.fast else 'FULL DETAIL (Search + Detail Pages)'}")
    print("============================================================\n")

    scraper = ZillowColoradoScraper(delay_seconds=args.delay)
    ncb_client = NoCodeBackendClient()
    existing_urls = {} if args.dry_run else ncb_client.get_existing_urls()
    seen_urls: Set[str] = set() if args.update_existing else set(existing_urls.keys())

    total_scraped = 0
    total_inserted = 0
    total_updated = 0

    # 1. Scrape explicit detail URLs if provided (or default examples when running CO only)
    detail_urls = args.detail_urls
    if not detail_urls and target_states == ["co"] and not args.fast:
        detail_urls = DEFAULT_EXAMPLE_URLS

    if detail_urls:
        featured_batch: List[Dict[str, Any]] = []
        for detail_url in detail_urls:
            print(f"🏠 Scraping featured detail page: {detail_url}")
            rec = scraper.scrape_detail_url(detail_url)
            if rec:
                featured_batch.append(rec)
                seen_urls.add(detail_url)
                total_scraped += 1
                print(
                    f"   ✓ {rec['title']} | {rec['price_display']} (base: ${rec.get('base_rent')}, fees: ${rec.get('required_monthly_fee_min')}) | "
                    f"{rec['bedrooms']} bd / {rec['bathrooms']} ba / {rec['sqft']} sqft | "
                    f"Agent: {rec.get('agent_name')} ({rec.get('contact_phone')})"
                )
            time.sleep(args.delay)
        if featured_batch and not args.dry_run:
            res = ncb_client.upsert_records(
                featured_batch, existing_cache=existing_urls, update_existing=True
            )
            total_inserted += res["inserted"]
            total_updated += res["updated"]

    # 2. Iterate state-by-state and sync incrementally to NoCodeBackend
    for state_code in target_states:
        if total_scraped >= args.max_listings:
            break

        state_upper = state_code.upper()
        state_urls = build_state_search_urls(
            [state_code], pages_per_state=args.pages_per_state, include_houses=True
        )
        all_raw_cards: List[Dict[str, Any]] = []
        for _, s_url in state_urls:
            cards = scraper.extract_search_results(s_url)
            if cards:
                all_raw_cards.extend(cards)
            time.sleep(args.delay)

        # Separate single-family /homedetails/ cards and /apartments/ building cards and interleave 1:1
        homedetail_cards = [c for c in all_raw_cards if "/homedetails/" in str(c.get("detailUrl") or "")]
        building_cards = [c for c in all_raw_cards if "/homedetails/" not in str(c.get("detailUrl") or "")]
        interleaved_cards: List[Dict[str, Any]] = []
        max_len = max(len(homedetail_cards), len(building_cards))
        for idx in range(max_len):
            if idx < len(homedetail_cards):
                interleaved_cards.append(homedetail_cards[idx])
            if idx < len(building_cards):
                interleaved_cards.append(building_cards[idx])

        state_records: List[Dict[str, Any]] = []
        for card in interleaved_cards:
            if len(state_records) >= args.per_state or total_scraped >= args.max_listings:
                break
            base_rec = scraper.parse_search_card_fallback(card, default_state=state_upper)
            detail_url = base_rec.get("listing_url", "")
            if not detail_url or detail_url in seen_urls:
                continue

            seen_urls.add(detail_url)
            if args.fast:
                enriched = base_rec
            else:
                print(
                    f"🔎 [{state_upper} {len(state_records) + 1}/{args.per_state}] Enriching: {detail_url}"
                )
                try:
                    enriched = scraper.scrape_detail_url(detail_url, base_record=base_rec) or base_rec
                except Exception as exc:
                    print(f"   [!] Detail parse fallback for {detail_url}: {exc}")
                    enriched = base_rec
                time.sleep(args.delay)

            state_records.append(enriched)
            total_scraped += 1

        if state_records:
            if args.dry_run:
                print(f"   [Dry Run] Collected {len(state_records)} records for {state_upper}")
            else:
                res = ncb_client.upsert_records(
                    state_records,
                    existing_cache=existing_urls,
                    update_existing=args.update_existing,
                )
                total_inserted += res["inserted"]
                total_updated += res["updated"]
                print(
                    f"   ✅ {state_upper} Synced -> Inserted: {res['inserted']}, "
                    f"Updated: {res['updated']} (Session Synced: {total_inserted + total_updated})"
                )

    print("\n============================================================")
    print(f"🎉 Scrape Complete! Total Scraped: {total_scraped}")
    if not args.dry_run:
        print(f"🗄️  Database ({NCB_INSTANCE}.{NCB_TABLE}) -> Inserted: {total_inserted}, Updated: {total_updated}")
    print("============================================================")


if __name__ == "__main__":
    main()
