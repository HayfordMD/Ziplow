#!/usr/bin/env python3
"""
Zillow Nationwide & State Rentals Scraper -> NoCodeBackend (NCB) Database (`43023_ziplow_db`)

Scrapes rental listings across Colorado or all 50 US states + DC from:
  - https://www.zillow.com/<state>/rentals/ (and paginated /2_p/, /3_p/, ...)
  - https://www.zillow.com/<state>/rent-houses/
  - Individual Zillow `/homedetails/...` and `/apartments/...` pages

Extracts price, address, beds, baths, full bathrooms, sqft, total interior livable area,
room types, flooring, interior facts & features, full facts & features JSON, photos,
and virtual tour URLs, and stores them in NoCodeBackend (`43023_ziplow_db` -> `rentals`).
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
        return None
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
        """Builds a baseline record from a search result card prior to detail enrichment."""
        detail_url = card.get("detailUrl", "")
        if detail_url and detail_url.startswith("/"):
            detail_url = urljoin("https://www.zillow.com", detail_url)

        lat_long = card.get("latLong") or {}
        units = card.get("units") or []

        price_str = card.get("price") or ""
        beds = card.get("beds")
        baths = card.get("baths")
        sqft = card.get("area")

        if not price_str and units:
            first_unit = units[0]
            price_str = first_unit.get("price", "")
            if beds is None:
                beds = first_unit.get("beds")

        price_monthly = parse_int(price_str) or parse_int(card.get("minBaseRent"))
        if not price_str and price_monthly:
            price_str = f"${price_monthly:,}/mo"

        includes_fees = card.get("listPriceIncludesRequiredMonthlyFees", False)
        price_note = (
            "Total monthly price - This listing now includes required monthly fees in the total monthly price."
            if includes_fees
            else ""
        )

        is_building = bool(card.get("isBuilding") or "/apartments/" in detail_url or "/b/" in detail_url)
        prop_type = "Apartment / Building" if is_building else "Single Family / Home"

        title = card.get("buildingName") or card.get("statusText") or card.get("address") or ""
        sqft_int = parse_int(sqft)
        total_area_str = f"{sqft_int:,} sqft" if sqft_int else ""

        photos = []
        for p in (card.get("carouselPhotosComposable") or {}).get("photoData") or []:
            if isinstance(p, dict) and p.get("photoKey"):
                photos.append(
                    f"https://photos.zillowstatic.com/fp/{p['photoKey']}-p_e.jpg"
                )
        if not photos and card.get("imgSrc"):
            photos.append(card["imgSrc"])

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "zpid": str(card.get("zpid") or card.get("id") or ""),
            "listing_url": detail_url,
            "property_type": prop_type,
            "title": str(title)[:255],
            "address": str(card.get("address") or "")[:255],
            "street_address": str(card.get("addressStreet") or "")[:255],
            "city": str(card.get("addressCity") or "")[:100],
            "state": str(card.get("addressState") or default_state.upper())[:50],
            "zipcode": str(card.get("addressZipcode") or "")[:20],
            "price_display": str(price_str)[:100],
            "price_monthly": price_monthly,
            "price_note": price_note[:255],
            "bedrooms": parse_int(beds),
            "bathrooms": parse_float(baths),
            "full_bathrooms": parse_int(baths),
            "sqft": sqft_int,
            "total_interior_livable_area": total_area_str[:100],
            "room_types": "",
            "flooring": "",
            "interior_features": format_interior_summary(
                bedrooms=parse_int(beds),
                bathrooms=parse_float(baths),
                full_bathrooms=parse_int(baths),
                room_types="",
                flooring="",
                total_livable_area=total_area_str,
                virtual_tour_url="",
            ),
            "facts_and_features": json.dumps({"units": units, "rawCardStatus": card.get("statusText")}),
            "virtual_tour_url": "",
            "image_url": str(card.get("imgSrc") or "")[:500],
            "photos_json": json.dumps(photos[:15]),
            "latitude": str(lat_long.get("latitude") or "")[:50],
            "longitude": str(lat_long.get("longitude") or "")[:50],
            "description": "",
            "scraped_at": now_str,
        }

    def parse_homedetails_page(self, url: str, next_data: Dict[str, Any], base_record: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Extracts rich property & interior facts from a Zillow /homedetails/ page."""
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

        street = addr_obj.get("streetAddress") or prop.get("streetAddress") or ""
        city = addr_obj.get("city") or prop.get("city") or ""
        state = addr_obj.get("state") or prop.get("state") or (base_record or {}).get("state") or "CO"
        zipcode = addr_obj.get("zipcode") or prop.get("zipcode") or ""
        full_address = ", ".join(p for p in [street, city, f"{state} {zipcode}".strip()] if p)

        price_val = parse_int(prop.get("price"))
        price_display = f"${price_val:,}/mo" if price_val else ""
        includes_fees = prop.get("listPriceIncludesRequiredMonthlyFees", False)
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

        sqft_val = parse_int(prop.get("livingArea") or prop.get("livingAreaValue") or reso.get("livingArea"))
        total_livable_area = reso.get("livingArea") or (f"{sqft_val:,} sqft" if sqft_val else "")

        room_types = join_list(reso.get("roomTypes") or reso.get("rooms"))
        flooring = join_list(reso.get("flooring"))
        appliances = join_list(reso.get("appliances"))
        cooling = join_list(reso.get("cooling"))
        heating = join_list(reso.get("heating"))
        laundry = join_list(reso.get("laundryFeatures"))
        parking = join_list(reso.get("parkingFeatures"))
        pets = join_list(reso.get("allowedPets"))

        # Extract virtual tour URL
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
            },
        )

        # Collect non-empty resoFacts
        clean_reso = {
            k: v
            for k, v in reso.items()
            if v not in (None, [], {}, False, "")
        }

        photos = []
        for p in prop.get("responsivePhotos") or []:
            if isinstance(p, dict) and p.get("url"):
                photos.append(p["url"])
        image_url = photos[0] if photos else (base_record or {}).get("image_url", "")

        prop_type_list = reso.get("propertySubType") or []
        prop_type = (
            prop_type_list[0]
            if prop_type_list
            else (reso.get("homeType") or prop.get("homeType") or "Single Family Residence")
        )

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "zpid": str(prop.get("zpid") or comp_props.get("zpid") or ""),
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
            "price_note": price_note[:255],
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "full_bathrooms": full_bathrooms,
            "sqft": sqft_val,
            "total_interior_livable_area": str(total_livable_area)[:100],
            "room_types": room_types,
            "flooring": flooring[:255],
            "interior_features": interior_text,
            "facts_and_features": json.dumps(
                {
                    "interior": {
                        "bedrooms": bedrooms,
                        "bathrooms": bathrooms,
                        "full_bathrooms": full_bathrooms,
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
                        "fencing": reso.get("fencing"),
                        "patio_and_porch": reso.get("patioAndPorchFeatures"),
                        "year_built": prop.get("yearBuilt"),
                    },
                    "virtual_tour_url": virtual_tour_url,
                    "resoFacts": clean_reso,
                }
            ),
            "virtual_tour_url": str(virtual_tour_url)[:500],
            "image_url": str(image_url)[:500],
            "photos_json": json.dumps(photos[:15]),
            "latitude": str(prop.get("latitude") or (base_record or {}).get("latitude") or "")[:50],
            "longitude": str(prop.get("longitude") or (base_record or {}).get("longitude") or "")[:50],
            "description": str(prop.get("description") or ""),
            "scraped_at": now_str,
        }

    def parse_apartment_building_page(
        self, url: str, next_data: Dict[str, Any], base_record: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Extracts rich building, floor plan, amenity, and virtual tour details from /apartments/ pages."""
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

        building_name = building.get("buildingName") or (base_record or {}).get("title") or ""
        street = building.get("streetAddress") or (building.get("address") or {}).get("streetAddress") or ""
        city = building.get("city") or (building.get("address") or {}).get("city") or ""
        state = building.get("state") or (building.get("address") or {}).get("state") or (base_record or {}).get("state") or "CO"
        zipcode = building.get("zipcode") or (building.get("address") or {}).get("zipcode") or ""
        full_address = building.get("fullAddress") or ", ".join(
            p for p in [street, city, f"{state} {zipcode}".strip()] if p
        )

        floor_plans = building.get("floorPlans") or []
        ungrouped_units = building.get("ungroupedUnits") or []
        all_plans = floor_plans if floor_plans else ungrouped_units

        min_prices = []
        beds_list = []
        baths_list = []
        sqfts_list = []
        plan_summaries = []

        for fp in all_plans:
            if not isinstance(fp, dict):
                continue
            p_min = parse_int(fp.get("minPrice") or fp.get("price"))
            p_max = parse_int(fp.get("maxPrice") or fp.get("price"))
            b_val = parse_int(fp.get("beds"))
            ba_val = parse_float(fp.get("baths"))
            sq_val = parse_int(fp.get("sqft"))

            if p_min:
                min_prices.append(p_min)
            if b_val is not None:
                beds_list.append(b_val)
            if ba_val is not None:
                baths_list.append(ba_val)
            if sq_val:
                sqfts_list.append(sq_val)

            plan_summaries.append(
                {
                    "name": fp.get("name"),
                    "beds": b_val,
                    "baths": ba_val,
                    "sqft": sq_val,
                    "minPrice": p_min,
                    "maxPrice": p_max,
                    "unitCount": len(fp.get("units") or []),
                }
            )

        price_monthly = min(min_prices) if min_prices else (base_record or {}).get("price_monthly")
        max_price = max(min_prices) if min_prices else None
        if price_monthly and max_price and max_price > price_monthly:
            price_display = f"${price_monthly:,} - ${max_price:,}/mo"
        elif price_monthly:
            price_display = f"${price_monthly:,}+/mo"
        else:
            price_display = (base_record or {}).get("price_display") or ""

        includes_fees = building.get("listPriceIncludesRequiredMonthlyFees", False)
        price_note = (
            "Total monthly price - This listing now includes required monthly fees in the total monthly price."
            if includes_fees
            else "Base rent / Floor plan pricing"
        )

        bedrooms = min(beds_list) if beds_list else (base_record or {}).get("bedrooms")
        bathrooms = min(baths_list) if baths_list else (base_record or {}).get("bathrooms")
        full_bathrooms = int(bathrooms) if bathrooms is not None else None
        sqft_val = min(sqfts_list) if sqfts_list else (base_record or {}).get("sqft")
        if sqfts_list and min(sqfts_list) != max(sqfts_list):
            total_livable_area = f"{min(sqfts_list):,} - {max(sqfts_list):,} sqft"
        elif sqft_val:
            total_livable_area = f"{sqft_val:,} sqft"
        else:
            total_livable_area = ""

        # Extract amenities & interior features
        structured_amenities = building.get("structuredAmenities") or {}
        unit_features_groups = ((structured_amenities.get("unitFeatures") or {}).get("amenityGroups")) or []
        building_amenity_groups = ((structured_amenities.get("buildingAmenities") or {}).get("amenityGroups")) or []

        unit_features_map: Dict[str, str] = {}
        for grp in unit_features_groups:
            if isinstance(grp, dict) and grp.get("title"):
                unit_features_map[grp["title"]] = join_list(grp.get("amenities"))

        amenity_details = building.get("amenityDetails") or {}
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
        room_types = join_list(
            [fp.get("name") for fp in all_plans if isinstance(fp, dict) and fp.get("name")]
        )

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

        photos = []
        for p in building.get("galleryPhotos") or building.get("photos") or []:
            if isinstance(p, dict):
                mixed = (p.get("mixedSources") or {}).get("jpeg") or []
                u = p.get("url") or (mixed[-1].get("url") if mixed and isinstance(mixed[-1], dict) else "")
                if u:
                    photos.append(u)
        image_url = photos[0] if photos else (base_record or {}).get("image_url", "")

        building_key = (
            (comp_props.get("query") or {}).get("buildingKey")
            or building.get("lotId")
            or building.get("zpid")
            or (base_record or {}).get("zpid")
            or ""
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
            "price_note": price_note[:255],
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "full_bathrooms": full_bathrooms,
            "sqft": sqft_val,
            "total_interior_livable_area": str(total_livable_area)[:100],
            "room_types": room_types,
            "flooring": str(flooring)[:255],
            "interior_features": interior_text,
            "facts_and_features": json.dumps(
                {
                    "buildingName": building_name,
                    "floorPlans": plan_summaries,
                    "unitFeatures": unit_features_map,
                    "buildingAmenities": building_amenity_groups,
                    "commonUnitAmenities": building.get("commonUnitAmenities"),
                    "customAmenities": custom_raw,
                    "pets": amenity_details.get("pets"),
                    "virtualTourUrl": virtual_tour_url,
                }
            ),
            "virtual_tour_url": str(virtual_tour_url)[:500],
            "image_url": str(image_url)[:500],
            "photos_json": json.dumps(photos[:15]),
            "latitude": str(building.get("latitude") or (base_record or {}).get("latitude") or "")[:50],
            "longitude": str(building.get("longitude") or (base_record or {}).get("longitude") or "")[:50],
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
                    return existing
        except Exception as exc:
            print(f"  [!] Warning checking existing records via Data API: {exc}")

        if self.mcp_key:
            try:
                res = self.call_mcp_tool(
                    "execute_sql",
                    {
                        "database": self.instance,
                        "sql": f"SELECT id, listing_url FROM {self.table} LIMIT 5000;",
                    },
                )
                text_items = res.get("result", {}).get("content", [])
                for item in text_items:
                    txt = item.get("text", "")
                    if "listing_url" in txt:
                        try:
                            parsed = json.loads(txt)
                            rows = parsed.get("rows") or parsed.get("data") or []
                            for r in rows:
                                if isinstance(r, dict) and r.get("listing_url"):
                                    existing[r["listing_url"]] = r.get("id")
                        except Exception:
                            pass
            except Exception as exc:
                print(f"  [!] Warning checking existing records via MCP: {exc}")

        return existing

    def upsert_records(
        self, records: List[Dict[str, Any]], existing_cache: Optional[Dict[str, Any]] = None
    ) -> Dict[str, int]:
        """Inserts new records or updates existing ones in NoCodeBackend."""
        existing = existing_cache if existing_cache is not None else self.get_existing_urls()
        to_insert: List[Dict[str, Any]] = []
        updated_count = 0

        for rec in records:
            l_url = rec.get("listing_url", "")
            rec_id = existing.get(l_url)
            if rec_id and self.secret_key:
                try:
                    url = f"{self.data_api_url}/update/{self.table}?instance={self.instance}&id={rec_id}"
                    headers = {
                        "Content-Type": "application/json",
                        "X-Database-Instance": self.instance,
                        "Authorization": f"Bearer {self.secret_key}",
                    }
                    r = std_requests.post(url, headers=headers, json=rec, timeout=20)
                    if r.ok:
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
            # Prefer MCP `insert_mock_data` when NCB_MCP_KEY is configured
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
        description="Scrape Colorado or Nationwide US rentals from Zillow into NoCodeBackend (NCB)."
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
        default=2500,
        help="Maximum total listings to scrape across the run (default: 2500).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: extract directly from search result JSON without fetching each detail page.",
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
    print("🇺🇸  Zillow Rentals Scraper -> NoCodeBackend (NCB)")
    print(f"📌 Database Instance: {NCB_INSTANCE} (Table: {NCB_TABLE})")
    print(f"🌎 Target States ({len(target_states)}): {', '.join(s.upper() for s in target_states)}")
    print(f"⚡ Mode: {'FAST (Search Cards)' if args.fast else 'FULL DETAIL (Search + Detail Pages)'}")
    print("============================================================\n")

    scraper = ZillowColoradoScraper(delay_seconds=args.delay)
    ncb_client = NoCodeBackendClient()
    existing_urls = {} if args.dry_run else ncb_client.get_existing_urls()
    seen_urls: Set[str] = set(existing_urls.keys())

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
                    f"   ✓ {rec['title']} | {rec['price_display']} | "
                    f"{rec['bedrooms']} bd / {rec['bathrooms']} ba / {rec['sqft']} sqft"
                )
            time.sleep(args.delay)
        if featured_batch and not args.dry_run:
            res = ncb_client.upsert_records(featured_batch, existing_cache=existing_urls)
            total_inserted += res["inserted"]
            total_updated += res["updated"]

    # 2. Iterate state-by-state and sync incrementally to NoCodeBackend
    for state_code in target_states:
        if total_scraped >= args.max_listings:
            break

        state_upper = state_code.upper()
        state_urls = build_state_search_urls(
            [state_code], pages_per_state=args.pages_per_state, include_houses=not args.fast
        )
        card_lists: List[List[Dict[str, Any]]] = []
        for _, s_url in state_urls:
            cards = scraper.extract_search_results(s_url)
            if cards:
                card_lists.append(cards)
            time.sleep(args.delay)

        # Interleave cards from rentals and rent-houses
        interleaved_cards: List[Dict[str, Any]] = []
        max_len = max((len(c) for c in card_lists), default=0)
        for idx in range(max_len):
            for clist in card_lists:
                if idx < len(clist):
                    interleaved_cards.append(clist[idx])

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
                res = ncb_client.upsert_records(state_records, existing_cache=existing_urls)
                total_inserted += res["inserted"]
                total_updated += res["updated"]
                print(
                    f"   ✅ {state_upper} Synced -> Inserted: {res['inserted']}, "
                    f"Updated: {res['updated']} (Total DB Synced: {total_inserted + total_updated})"
                )

    print("\n============================================================")
    print(f"🎉 Scrape Complete! Total Scraped: {total_scraped}")
    if not args.dry_run:
        print(f"🗄️  Database ({NCB_INSTANCE}.{NCB_TABLE}) -> Inserted: {total_inserted}, Updated: {total_updated}")
    print("============================================================")


if __name__ == "__main__":
    main()
