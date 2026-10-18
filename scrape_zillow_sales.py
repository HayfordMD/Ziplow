#!/usr/bin/env python3
"""
Zillow High-Volume For Sale & Recently Sold Scraper -> NoCodeBackend (`43023_ziplow_db.sales`)

Supports gathering 10,000+ listings per state across all 50 US states + DC using:
  1. Zillow's `PUT /async-create-search-page-state` (`mapResults` + `listResults` — 500+ listings/call)
  2. 22 disjoint price bands + automatic 2x2 geographic quadrant subdivision for dense regions
  3. Automatic `SOLD` (`/<state>/sold/`) fallback for smaller states with <10,000 active For Sale listings
  4. High-throughput 500-row batch inserts into `43023_ziplow_db.sales` via NoCodeBackend MCP
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import datetime
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

from scrape_zillow_rentals import (
    NCB_DATA_API_URL,
    NCB_INSTANCE,
    NCB_MCP_KEY,
    NCB_MCP_URL,
    NCB_SECRET_KEY,
    NoCodeBackendClient,
    US_STATES,
    ZillowColoradoScraper,
    extract_insight_phrases,
    format_interior_summary,
    join_list,
    parse_float,
    parse_int,
)

load_dotenv()

SALES_TABLE = os.getenv("NCB_SALES_TABLE", "sales")

# 22 disjoint price bands (each returns up to ~500 mapResults per region/quadrant)
PRICE_BANDS: List[Tuple[int, int]] = [
    (0, 75000),
    (75001, 125000),
    (125001, 165000),
    (165001, 200000),
    (200001, 235000),
    (235001, 265000),
    (265001, 295000),
    (295001, 325000),
    (325001, 355000),
    (355001, 385000),
    (385001, 415000),
    (415001, 450000),
    (450001, 490000),
    (490001, 535000),
    (535001, 585000),
    (585001, 645000),
    (645001, 720000),
    (720001, 815000),
    (815001, 950000),
    (950001, 1200000),
    (1200001, 1750000),
    (1750001, 100000000),
]


def format_epoch_ms(val: Any) -> str:
    """Formats a millisecond epoch timestamp (e.g. dateSold) as YYYY-MM-DD."""
    if not val:
        return ""
    if isinstance(val, str) and not val.isdigit():
        return val
    try:
        ts = int(val)
        if ts > 10_000_000_000:
            ts = ts // 1000
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return str(val)


def subdivide_bounds(bounds: Dict[str, float]) -> List[Dict[str, float]]:
    """Splits a geographic bounding box into 4 quadrants (SW, SE, NW, NE)."""
    lat_mid = (bounds["south"] + bounds["north"]) / 2.0
    lon_mid = (bounds["west"] + bounds["east"]) / 2.0
    return [
        {"west": bounds["west"], "east": lon_mid, "south": bounds["south"], "north": lat_mid},
        {"west": lon_mid, "east": bounds["east"], "south": bounds["south"], "north": lat_mid},
        {"west": bounds["west"], "east": lon_mid, "south": lat_mid, "north": bounds["north"]},
        {"west": lon_mid, "east": bounds["east"], "south": lat_mid, "north": bounds["north"]},
    ]


class ZillowSalesScraper(ZillowColoradoScraper):
    """Scrapes Zillow For Sale and Recently Sold search pages, mapResults, and /homedetails/ pages."""

    def parse_sale_search_card(
        self, card: Dict[str, Any], category: str = "FOR_SALE", default_state: str = "CO"
    ) -> Dict[str, Any]:
        """Extracts all available fields and picture links from a For Sale or Sold search/map card."""
        detail_url = card.get("detailUrl", "")
        if detail_url and detail_url.startswith("/"):
            detail_url = urljoin("https://www.zillow.com", detail_url)

        lat_long = card.get("latLong") or {}
        hdp = (card.get("hdpData") or {}).get("homeInfo") or {}
        ff = card.get("factsAndFeatures") or {}
        recs = card.get("listCardRecommendation") or {}
        flex_recs = recs.get("flexFieldRecommendations") or []

        raw_status = str(card.get("statusType") or hdp.get("homeStatus") or category).upper()
        listing_category = "SOLD" if "SOLD" in raw_status else "FOR_SALE"

        price_val = (
            parse_int(card.get("unformattedPrice"))
            or parse_int(hdp.get("price"))
            or parse_int(card.get("price"))
        )
        price_str = card.get("price") or (f"${price_val:,}" if price_val else "")
        last_sold_price = price_val if listing_category == "SOLD" else parse_int(hdp.get("lastSoldPrice"))
        date_sold = format_epoch_ms(hdp.get("dateSold"))

        beds = parse_int(card.get("beds") if card.get("beds") is not None else hdp.get("bedrooms"))
        baths = parse_float(card.get("baths") if card.get("baths") is not None else hdp.get("bathrooms"))
        sqft_int = parse_int(card.get("area") if card.get("area") is not None else hdp.get("livingArea"))
        total_area_str = f"{sqft_int:,} sqft" if sqft_int else ""
        price_per_sqft = (
            round(price_val / sqft_int, 2)
            if (price_val and sqft_int and sqft_int > 0)
            else None
        )

        lot_val = hdp.get("lotAreaValue")
        lot_unit = hdp.get("lotAreaUnit") or "sqft"
        lot_size_str = f"{round(float(lot_val), 2)} {lot_unit}".strip() if lot_val else ""

        # Extract all picture links from carouselPhotosComposable / carouselPhotos / imgSrc
        photos: List[str] = []
        composable = card.get("carouselPhotosComposable") or {}
        base_photo_url = composable.get("baseUrl") or "https://photos.zillowstatic.com/fp/{photoKey}-p_e.jpg"
        for p in composable.get("photoData") or []:
            if isinstance(p, dict) and p.get("photoKey"):
                photos.append(base_photo_url.replace("{photoKey}", p["photoKey"]))
        if not photos:
            for p in card.get("carouselPhotos") or []:
                if isinstance(p, dict) and p.get("url"):
                    photos.append(p["url"])
        if not photos and card.get("imgSrc"):
            photos.append(card["imgSrc"])
        image_url = photos[0] if photos else str(card.get("imgSrc") or "")

        time_on_zillow_str = ""
        for fr in flex_recs:
            if isinstance(fr, dict) and fr.get("contentType") == "timeOnInfo":
                time_on_zillow_str = str(fr.get("displayString") or "")
        if not time_on_zillow_str and card.get("timeOnZillow"):
            days_calc = int(card["timeOnZillow"]) // 86400000
            time_on_zillow_str = f"{days_calc} days" if days_calc >= 0 else ""

        days_on_zillow = parse_int(hdp.get("daysOnZillow"))
        if days_on_zillow is not None and days_on_zillow < 0:
            days_on_zillow = (
                int(card["timeOnZillow"]) // 86400000
                if card.get("timeOnZillow")
                else None
            )

        agent_name = str(card.get("info6String") or "")
        mls_name = str(card.get("info2String") or "").replace("Listing provided by ", "").strip()
        brokerage_name = str(card.get("brokerName") or "")
        on_market_date = str(card.get("availabilityDate") or "")

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        title = card.get("address") or hdp.get("streetAddress") or card.get("statusText") or ""
        return {
            "zpid": str(card.get("zpid") or card.get("id") or hdp.get("zpid") or ""),
            "listing_url": detail_url,
            "listing_category": listing_category,
            "home_status": raw_status[:50],
            "property_type": str(hdp.get("homeType") or "SINGLE_FAMILY")[:100],
            "title": str(title)[:255],
            "address": str(card.get("address") or "")[:255],
            "street_address": str(card.get("addressStreet") or hdp.get("streetAddress") or "")[:255],
            "city": str(card.get("addressCity") or hdp.get("city") or "")[:100],
            "state": str(card.get("addressState") or hdp.get("state") or default_state.upper())[:50],
            "zipcode": str(card.get("addressZipcode") or hdp.get("zipcode") or "")[:20],
            "county": "",
            "county_fips": "",
            "neighborhood": "",
            "timezone": "",
            "price_display": str(price_str)[:100],
            "price": price_val,
            "last_sold_price": last_sold_price,
            "date_sold": date_sold[:100],
            "price_per_sqft": price_per_sqft,
            "zestimate": parse_int(card.get("zestimate") or hdp.get("zestimate")),
            "rent_zestimate": parse_int(hdp.get("rentZestimate")),
            "hoa_fee": None,
            "property_tax_rate": None,
            "tax_annual_amount": None,
            "tax_assessed_value": parse_int(hdp.get("taxAssessedValue")),
            "price_note": f"Sold on {date_sold}" if (listing_category == "SOLD" and date_sold) else str(card.get("statusText") or listing_category)[:255],
            "price_history_json": "[]",
            "bedrooms": beds,
            "bathrooms": baths,
            "full_bathrooms": parse_int(ff.get("fullBathroomCount")) or parse_int(baths),
            "half_bathrooms": parse_int(ff.get("halfBathroomCount")),
            "sqft": sqft_int,
            "total_interior_livable_area": total_area_str[:100],
            "year_built": None,
            "lot_size": lot_size_str[:100],
            "parcel_number": "",
            "room_types": "",
            "flooring": "",
            "appliances": "",
            "heating": "",
            "cooling": "Air Conditioning" if ff.get("hasAirConditioning") else "",
            "laundry": "",
            "parking": "",
            "exterior_features": "",
            "interior_features": format_interior_summary(
                bedrooms=beds,
                bathrooms=baths,
                full_bathrooms=parse_int(ff.get("fullBathroomCount")) or parse_int(baths),
                room_types="",
                flooring="",
                total_livable_area=total_area_str,
                virtual_tour_url="",
            ),
            "amenities_json": json.dumps({"factsAndFeatures": ff}),
            "home_insights": str(card.get("flexFieldText") or ""),
            "facts_and_features": json.dumps(
                {
                    "hdpData": hdp,
                    "factsAndFeatures": ff,
                    "statusText": card.get("statusText"),
                    "marketingStatus": card.get("marketingStatusSimplifiedCd"),
                }
            ),
            "walk_score": None,
            "transit_score": None,
            "bike_score": None,
            "neighborhood_details_json": "{}",
            "virtual_tour_url": "",
            "floor_plan_url": "",
            "has_3d_model": 1 if card.get("has3DModel") else 0,
            "image_url": image_url[:500],
            "photo_count": len(photos),
            "photos_json": json.dumps(photos),
            "videos_and_media_json": json.dumps(
                {"has3DModel": card.get("has3DModel"), "hasVideo": card.get("hasVideo")}
            ),
            "latitude": str(lat_long.get("latitude") or hdp.get("latitude") or "")[:50],
            "longitude": str(lat_long.get("longitude") or hdp.get("longitude") or "")[:50],
            "days_on_zillow": days_on_zillow,
            "time_on_zillow": time_on_zillow_str[:100],
            "on_market_date": on_market_date[:100],
            "agent_name": agent_name[:255],
            "brokerage_name": brokerage_name[:255],
            "contact_phone": "",
            "mls_id": str(card.get("palsId") or "")[:100],
            "mls_name": mls_name[:150],
            "provider_listing_id": str(card.get("providerListingId") or "")[:100],
            "schools_json": "[]",
            "description": "",
            "scraped_at": now_str,
        }

    _shared_csrf_token: Optional[str] = None

    def get_graphql_csrf(self) -> str:
        """Fetches and caches an x-csrf-token from Zillow Group's Trulia web frontend."""
        if ZillowSalesScraper._shared_csrf_token:
            return ZillowSalesScraper._shared_csrf_token
        try:
            resp = self.session.get("https://www.trulia.com/CA/", timeout=20)
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                resp.text,
            )
            if m:
                d = json.loads(m.group(1))
                tok = (
                    d.get("props", {})
                    .get("__TRULIA_HYDRATION_INTERNAL__", {})
                    .get("headers", {})
                    .get("x-csrf-token")
                )
                if tok:
                    ZillowSalesScraper._shared_csrf_token = tok
                    return tok
        except Exception as exc:
            print(f"  [!] Warning fetching GraphQL CSRF token: {exc}")
        return ""

    def parse_graphql_home(
        self, home: Dict[str, Any], category: str = "FOR_SALE", default_state: str = "CO"
    ) -> Dict[str, Any]:
        """Maps a HOME_Property from searchHomesByDetails into the 75-column sales schema."""
        t_map: Dict[str, str] = {}
        for item in home.get("tracking") or []:
            if isinstance(item, dict) and item.get("key"):
                t_map[str(item["key"])] = str(item.get("value") or "")

        eight_bit: Dict[str, str] = {}
        for part in t_map.get("item", "").split(";"):
            if "eightBitItem|traditional:" in part:
                kv = part.split("eightBitItem|traditional:", 1)[-1]
                if ":" in kv:
                    k, v = kv.split(":", 1)
                    eight_bit[k.strip()] = v.strip()

        typed_id = str(home.get("typedHomeId") or "")
        zpid = t_map.get("zPID") or typed_id.replace("_ZPID", "").strip()

        raw_url = str(home.get("url") or "")
        if raw_url.startswith("/home/"):
            slug = raw_url[len("/home/") :].strip("/")
            if zpid and slug.endswith(f"-{zpid}"):
                slug = slug[: -(len(zpid) + 1)]
            listing_url = f"https://www.zillow.com/homedetails/{slug}/{zpid}_zpid/"
        elif raw_url.startswith("/"):
            listing_url = f"https://www.zillow.com{raw_url}"
        else:
            listing_url = raw_url

        loc = home.get("location") or {}
        coords = loc.get("coordinates") or {}
        street = str(loc.get("streetAddress") or "")
        city = str(loc.get("city") or t_map.get("listingCity") or "")
        state = str(loc.get("stateCode") or t_map.get("listingState") or default_state).upper()
        zipcode = str(loc.get("zipCode") or t_map.get("listingZip") or "")
        full_address = str(
            loc.get("fullLocation")
            or ", ".join(p for p in [street, city, f"{state} {zipcode}".strip()] if p)
        )

        cur_status = home.get("currentStatus") or {}
        is_sold = bool(cur_status.get("isRecentlySold")) or category == "SOLD"
        listing_category = "SOLD" if is_sold else "FOR_SALE"
        home_status = "RECENTLY_SOLD" if is_sold else "FOR_SALE"

        price_obj = home.get("price") or {}
        price_val = parse_int(price_obj.get("price")) or parse_int(t_map.get("listingPrice"))
        price_display = str(price_obj.get("formattedPrice") or (f"${price_val:,}" if price_val else ""))
        last_sold_price = price_val if is_sold else None

        beds_obj = home.get("bedrooms") or {}
        baths_obj = home.get("bathrooms") or {}
        beds = parse_int(beds_obj.get("value") if beds_obj.get("value") is not None else beds_obj.get("formattedValue"))
        baths = parse_float(
            baths_obj.get("value") if baths_obj.get("value") is not None else baths_obj.get("formattedValue")
        )

        floor_obj = home.get("floorSpace") or {}
        sqft_int = parse_int(floor_obj.get("formattedDimension"))
        total_area_str = str(floor_obj.get("formattedDimension") or (f"{sqft_int:,} sqft" if sqft_int else ""))

        pps_raw = eight_bit.get("Price/Sqft", "")
        price_per_sqft = parse_float(pps_raw) or (
            round(price_val / sqft_int, 2) if (price_val and sqft_int and sqft_int > 0) else None
        )

        desc_text = str((home.get("description") or {}).get("value") or "")
        year_built = parse_int(eight_bit.get("Year Built"))
        if not year_built and desc_text:
            ym = re.search(r"built in (\d{4})", desc_text, re.IGNORECASE)
            if ym:
                year_built = int(ym.group(1))

        lot_obj = home.get("lotSize") or {}
        lot_size_str = str(lot_obj.get("formattedDimension") or "")
        if not lot_size_str and desc_text:
            lm = re.search(r"lot size of ([\d,.]+ (?:sqft|acres?))", desc_text, re.IGNORECASE)
            if lm:
                lot_size_str = lm.group(1)

        parking_str = eight_bit.get("Parking", "")
        prop_type = str(
            (home.get("propertyType") or {}).get("value")
            or eight_bit.get("Property Type")
            or t_map.get("propertyType")
            or "SINGLE_FAMILY"
        ).upper()

        media = home.get("media") or {}
        photos: List[str] = []
        for p in media.get("photos") or []:
            if isinstance(p, dict):
                u = (p.get("url") or {}).get("large") or (p.get("url") or {}).get("medium") or ""
                if u:
                    m_fp = re.search(r"zillowstatic/fp/([a-f0-9]+)-full\.jpg", u)
                    if m_fp:
                        u = f"https://photos.zillowstatic.com/fp/{m_fp.group(1)}-p_e.jpg"
                    photos.append(u)
        if not photos:
            hero_u = ((media.get("heroImage") or {}).get("url") or {}).get("medium") or ""
            if hero_u:
                m_fp = re.search(r"zillowstatic/fp/([a-f0-9]+)-full\.jpg", hero_u)
                if m_fp:
                    hero_u = f"https://photos.zillowstatic.com/fp/{m_fp.group(1)}-p_e.jpg"
                photos.append(hero_u)

        image_url = photos[0] if photos else ""
        photo_count = len(photos)

        active_listing = home.get("activeListing") or {}
        provider = active_listing.get("provider") or {}
        agent_name = str((provider.get("listingAgent") or {}).get("name") or "")
        broker_name = str((provider.get("broker") or {}).get("name") or "")
        if not broker_name:
            summary_list = provider.get("summary") or []
            if summary_list and isinstance(summary_list, list):
                broker_name = str(summary_list[0] or "")
        date_listed = str(active_listing.get("dateListed") or "")[:10]

        has_3d = 1 if (media.get("hasThreeDHome") or t_map.get("isthreeDTourAvaliable") == "1") else 0
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "zpid": str(zpid)[:50],
            "listing_url": listing_url[:500],
            "listing_category": listing_category,
            "home_status": home_status,
            "property_type": prop_type[:100],
            "title": full_address[:255],
            "address": full_address[:255],
            "street_address": street[:255],
            "city": city[:100],
            "state": state[:50],
            "zipcode": zipcode[:20],
            "county": "",
            "county_fips": "",
            "neighborhood": "",
            "timezone": "",
            "price_display": price_display[:100],
            "price": price_val,
            "last_sold_price": last_sold_price,
            "date_sold": "",
            "price_per_sqft": price_per_sqft,
            "zestimate": None,
            "rent_zestimate": None,
            "hoa_fee": None,
            "property_tax_rate": None,
            "tax_annual_amount": None,
            "tax_assessed_value": None,
            "price_note": listing_category,
            "price_history_json": "[]",
            "bedrooms": beds,
            "bathrooms": baths,
            "full_bathrooms": int(baths) if baths else None,
            "half_bathrooms": None,
            "sqft": sqft_int,
            "total_interior_livable_area": total_area_str[:100],
            "year_built": year_built,
            "lot_size": lot_size_str[:100],
            "parcel_number": "",
            "room_types": "",
            "flooring": "",
            "appliances": "",
            "heating": "",
            "cooling": "",
            "laundry": "",
            "parking": parking_str[:255],
            "exterior_features": "",
            "interior_features": format_interior_summary(
                bedrooms=beds,
                bathrooms=baths,
                full_bathrooms=int(baths) if baths else None,
                room_types="",
                flooring="",
                total_livable_area=total_area_str,
                virtual_tour_url="",
                extra_features={"Parking": parking_str} if parking_str else None,
            ),
            "amenities_json": json.dumps(eight_bit),
            "home_insights": "",
            "facts_and_features": json.dumps(
                {
                    "propertyType": prop_type,
                    "yearBuilt": year_built,
                    "lotSize": lot_size_str,
                    "parking": parking_str,
                    "pricePerSqft": price_per_sqft,
                }
            ),
            "walk_score": None,
            "transit_score": None,
            "bike_score": None,
            "neighborhood_details_json": "{}",
            "virtual_tour_url": "",
            "floor_plan_url": "",
            "has_3d_model": has_3d,
            "image_url": image_url[:500],
            "photo_count": photo_count,
            "photos_json": json.dumps(photos[:12]),
            "videos_and_media_json": json.dumps(
                {"has3DModel": bool(has_3d), "hasVideo": bool(media.get("hasVideo"))}
            ),
            "latitude": str(coords.get("latitude") or t_map.get("locationLat") or "")[:50],
            "longitude": str(coords.get("longitude") or t_map.get("locationLon") or "")[:50],
            "days_on_zillow": None,
            "time_on_zillow": "",
            "on_market_date": date_listed,
            "agent_name": agent_name[:255],
            "brokerage_name": broker_name[:255],
            "contact_phone": "",
            "mls_id": str(t_map.get("maloneID") or "")[:100],
            "mls_name": "",
            "provider_listing_id": str(t_map.get("listingID") or "")[:100],
            "schools_json": "[]",
            "description": desc_text,
            "scraped_at": now_str,
        }

    def fetch_graphql_homes_batch(
        self,
        state_upper: str,
        search_type: str,
        min_price: int = 0,
        max_price: str = "*",
        sort_type: str = "PRICE",
        ascending: bool = True,
        limit: int = 1000,
        extra_filters: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Queries Zillow Group's GraphQL searchHomesByDetails endpoint for up to 1,000 homes per call."""
        csrf = self.get_graphql_csrf()
        query = """
        query SearchStateHomes($searchDetails: SEARCHDETAILS_Input!) {
          searchHomesByDetails(searchDetails: $searchDetails) {
            totalHomes
            homes {
              __typename
              typedHomeId
              url
              description { value }
              location {
                city stateCode zipCode streetAddress
                fullLocation: formattedLocation(formatType: STREET_CITY_STATE_ZIP)
                coordinates { latitude longitude }
              }
              price {
                formattedPrice
                ... on HOME_SinglePrice { price currencyCode }
              }
              floorSpace { formattedDimension }
              lotSize {
                ... on HOME_SingleDimension { formattedDimension(minDecimalDigits: 2, maxDecimalDigits: 2) }
              }
              bedrooms {
                formattedValue(formatType: COMMON_ABBREVIATION)
                ... on HOME_FixedBedrooms { value }
              }
              bathrooms {
                formattedValue(formatType: COMMON_ABBREVIATION)
                ... on HOME_FixedBathrooms { value }
              }
              tracking { key value }
              media {
                hasThreeDHome
                hasVideo
                heroImage { url { medium } }
                photos { url { large } }
              }
              ... on HOME_Property {
                propertyType { value }
                currentStatus { isRecentlySold isActiveForSale }
                activeListing {
                  dateListed
                  provider {
                    summary(formatType: SHORT)
                    listingAgent { name }
                    broker { name }
                  }
                }
              }
            }
          }
        }
        """
        filters: Dict[str, Any] = {
            "sort": {"type": sort_type, "ascending": ascending},
            "limit": limit,
        }
        if min_price > 0 or max_price != "*":
            filters["price"] = {"min": str(min_price), "max": str(max_price)}
        if extra_filters:
            filters.update(extra_filters)

        variables = {
            "searchDetails": {
                "searchType": search_type,
                "location": {"states": [{"state": state_upper}]},
                "filters": filters,
            }
        }
        headers = {
            "Content-Type": "application/json",
            "x-csrf-token": csrf,
            "Origin": "https://www.trulia.com",
            "Referer": f"https://www.trulia.com/{state_upper}/",
        }
        for attempt in range(3):
            try:
                resp = self.session.post(
                    "https://www.trulia.com/graphql?operation_name=SearchStateHomes",
                    json={"operationName": "SearchStateHomes", "query": query, "variables": variables},
                    headers=headers,
                    timeout=30,
                )
                if resp.status_code == 200:
                    rj = resp.json()
                    res = (rj.get("data") or {}).get("searchHomesByDetails") or {}
                    return int(res.get("totalHomes") or 0), (res.get("homes") or [])
                if resp.status_code == 403:
                    ZillowSalesScraper._shared_csrf_token = None
                    csrf = self.get_graphql_csrf()
                    headers["x-csrf-token"] = csrf
            except Exception as exc:
                if attempt == 2:
                    print(f"  [!] GraphQL error for {state_upper} ({search_type}, min={min_price}): {exc}")
                time.sleep(1.0)
        return 0, []

    def gather_high_volume_state(
        self,
        state_code: str,
        target_count: int,
        seen_urls: Set[str],
        categories: List[str],
        delay: float = 0.05,
        seen_zpids: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Gathers up to `target_count` (e.g. 10,000+) unique listings for a state using
        Zillow Group's 1,000-listing/request GraphQL price-cursor + sqft-band engine (`FOR_SALE` + `SOLD`).
        """
        state_clean = state_code.lower().strip()
        state_upper = state_clean.upper()
        collected: List[Dict[str, Any]] = []
        if seen_zpids is None:
            seen_zpids = set()

        sqft_bands = [
            ("1", "950"),
            ("951", "1200"),
            ("1201", "1400"),
            ("1401", "1600"),
            ("1601", "1800"),
            ("1801", "2050"),
            ("2051", "2350"),
            ("2351", "2750"),
            ("2751", "3300"),
            ("3301", "4200"),
            ("4201", "100000"),
        ]

        for cat in categories:
            if len(collected) >= target_count:
                break
            search_type = "SOLD" if cat == "sold" else "FOR_SALE"
            cat_label = search_type
            base_extra = {"soldWithin": 36} if search_type == "SOLD" else {}

            # Pass 1: Ascending price cursor from $1 upward (1,000 homes per call)
            min_price = 1
            for _ in range(45):
                if len(collected) >= target_count:
                    break
                _, homes = self.fetch_graphql_homes_batch(
                    state_upper=state_upper,
                    search_type=search_type,
                    min_price=min_price,
                    max_price="*",
                    sort_type="PRICE",
                    ascending=True,
                    limit=1000,
                    extra_filters=base_extra,
                )
                if not homes:
                    break

                batch_prices: List[int] = []
                added_in_step = 0
                for h in homes:
                    if len(collected) >= target_count:
                        break
                    rec = self.parse_graphql_home(h, category=cat_label, default_state=state_upper)
                    u = rec.get("listing_url", "")
                    z = rec.get("zpid", "")
                    p = rec.get("price")
                    if isinstance(p, int) and p >= min_price:
                        batch_prices.append(p)
                    if not u or u in seen_urls or (z and z in seen_zpids):
                        continue
                    seen_urls.add(u)
                    if z:
                        seen_zpids.add(z)
                    collected.append(rec)
                    added_in_step += 1

                if len(homes) < 100 or not batch_prices:
                    break

                max_p = max(batch_prices)
                if max_p > min_price and added_in_step > 0:
                    min_price = max_p
                else:
                    min_price = max_p + 1

                if delay > 0:
                    time.sleep(delay)

            # Pass 2: If still below target_count (e.g. SOLD in non-disclosure states or small states), sweep squareFeet bands
            if len(collected) < target_count:
                for sf_min, sf_max in sqft_bands:
                    if len(collected) >= target_count:
                        break
                    for asc in (False, True):
                        if len(collected) >= target_count:
                            break
                        tot_sf, sf_homes = self.fetch_graphql_homes_batch(
                            state_upper=state_upper,
                            search_type=search_type,
                            min_price=0,
                            max_price="*",
                            sort_type="DATE",
                            ascending=asc,
                            limit=1000,
                            extra_filters={**base_extra, "squareFeet": {"min": sf_min, "max": sf_max}},
                        )
                        for h in sf_homes:
                            if len(collected) >= target_count:
                                break
                            rec = self.parse_graphql_home(h, category=cat_label, default_state=state_upper)
                            u = rec.get("listing_url", "")
                            z = rec.get("zpid", "")
                            if not u or u in seen_urls or (z and z in seen_zpids):
                                continue
                            seen_urls.add(u)
                            if z:
                                seen_zpids.add(z)
                            collected.append(rec)
                        if delay > 0:
                            time.sleep(delay)
                        if tot_sf <= 1000:
                            break

            # Pass 3: Unfiltered newest/oldest sweep (catches lots/land with null sqft and null price)
            if len(collected) < target_count:
                for asc in (False, True):
                    if len(collected) >= target_count:
                        break
                    _, newest_homes = self.fetch_graphql_homes_batch(
                        state_upper=state_upper,
                        search_type=search_type,
                        min_price=0,
                        max_price="*",
                        sort_type="DATE",
                        ascending=asc,
                        limit=1000,
                    )
                    for h in newest_homes:
                        if len(collected) >= target_count:
                            break
                        rec = self.parse_graphql_home(h, category=cat_label, default_state=state_upper)
                        u = rec.get("listing_url", "")
                        z = rec.get("zpid", "")
                        if not u or u in seen_urls or (z and z in seen_zpids):
                            continue
                        seen_urls.add(u)
                        if z:
                            seen_zpids.add(z)
                        collected.append(rec)

        return collected

    def enrich_sale_detail_url(
        self, url: str, base_record: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Scrapes full /homedetails/ page for a For Sale or Sold property including all high-res photo links."""
        base = base_record or {}
        next_data = self.fetch_next_data(url)
        if not next_data:
            return base_record

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
        rich_media = prop.get("richMedia") or {}

        street = addr_obj.get("streetAddress") or prop.get("streetAddress") or base.get("street_address") or ""
        city = addr_obj.get("city") or prop.get("city") or base.get("city") or ""
        state = addr_obj.get("state") or prop.get("state") or base.get("state") or "CO"
        zipcode = addr_obj.get("zipcode") or prop.get("zipcode") or base.get("zipcode") or ""
        full_address = ", ".join(p for p in [street, city, f"{state} {zipcode}".strip()] if p)

        home_status = str(prop.get("homeStatus") or base.get("home_status") or "FOR_SALE").upper()
        listing_category = "SOLD" if "SOLD" in home_status else "FOR_SALE"

        price_val = parse_int(prop.get("price")) or base.get("price")
        last_sold_price = parse_int(prop.get("lastSoldPrice")) or (price_val if listing_category == "SOLD" else base.get("last_sold_price"))
        date_sold = (
            str(prop.get("dateSoldString") or "")
            or format_epoch_ms(prop.get("dateSold"))
            or base.get("date_sold")
            or ""
        )
        price_display = f"${price_val:,}" if price_val else (base.get("price_display") or "")

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

        # Extract all high-res picture links
        photos: List[str] = []
        for p in prop.get("responsivePhotos") or []:
            if isinstance(p, dict):
                jpegs = (p.get("mixedSources") or {}).get("jpeg") or []
                hi_res = jpegs[-1].get("url") if (jpegs and isinstance(jpegs[-1], dict)) else None
                u = hi_res or p.get("url")
                if u and u not in photos:
                    photos.append(u)
        if not photos:
            for p in prop.get("originalPhotos") or []:
                if isinstance(p, dict):
                    jpegs = (p.get("mixedSources") or {}).get("jpeg") or []
                    u = jpegs[-1].get("url") if (jpegs and isinstance(jpegs[-1], dict)) else p.get("url")
                    if u and u not in photos:
                        photos.append(u)
        if not photos and base.get("photos_json"):
            try:
                photos = json.loads(base["photos_json"])
            except Exception:
                pass
        image_url = photos[0] if photos else (prop.get("hiResImageLink") or prop.get("desktopWebHdpImageLink") or base.get("image_url") or "")

        room_types = join_list(reso.get("roomTypes") or reso.get("rooms"))
        flooring = join_list(reso.get("flooring"))
        appliances = join_list(reso.get("appliances"))
        cooling = join_list(reso.get("cooling"))
        heating = join_list(reso.get("heating"))
        laundry = join_list(reso.get("laundryFeatures"))
        parking = join_list(reso.get("parkingFeatures") or reso.get("otherParking"))

        ext_parts: List[str] = []
        for ext_val in [
            reso.get("fencing"),
            join_list(reso.get("patioAndPorchFeatures")),
            join_list(reso.get("exteriorFeatures")),
            join_list(reso.get("poolFeatures")),
            join_list(reso.get("roofType")),
            join_list(reso.get("constructionMaterials")),
        ]:
            if ext_val:
                ext_parts.append(str(ext_val))
        exterior_features = "; ".join(ext_parts)

        virtual_tour_url = (
            prop.get("virtualTourUrl")
            or reso.get("virtualTour")
            or (prop.get("thirdPartyVirtualTour") or {}).get("lightboxUrl")
            or (prop.get("thirdPartyVirtualTour") or {}).get("externalUrl")
            or ""
        )
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
            },
        )

        clean_reso = {
            k: v
            for k, v in reso.items()
            if v not in (None, [], {}, False, "")
        }

        lot_area_val = prop.get("lotAreaValue") or reso.get("lotAreaValue")
        lot_area_units = prop.get("lotAreaUnits") or prop.get("lotAreaUnit") or reso.get("lotAreaUnits") or "sqft"
        lot_size_str = f"{lot_area_val} {lot_area_units}".strip() if lot_area_val else str(reso.get("lotSize") or base.get("lot_size") or "")

        hoa_fee = parse_int(prop.get("monthlyHoaFee") or reso.get("hoaFeeTotal") or reso.get("associationFee"))
        prop_tax_rate = parse_float(prop.get("propertyTaxRate"))

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "zpid": str(prop.get("zpid") or comp_props.get("zpid") or base.get("zpid") or ""),
            "listing_url": url,
            "listing_category": listing_category,
            "home_status": home_status[:50],
            "property_type": str(prop.get("homeType") or reso.get("homeType") or "SINGLE_FAMILY")[:100],
            "title": full_address[:255],
            "address": full_address[:255],
            "street_address": str(street)[:255],
            "city": str(city)[:100],
            "state": str(state)[:50],
            "zipcode": str(zipcode)[:20],
            "county": str(prop.get("county") or "")[:100],
            "county_fips": str(prop.get("countyFIPS") or "")[:20],
            "neighborhood": str(
                addr_obj.get("neighborhood") or addr_obj.get("subdivision") or reso.get("subdivisionName") or ""
            )[:150],
            "timezone": str(prop.get("timeZone") or "")[:100],
            "price_display": price_display[:100],
            "price": price_val,
            "last_sold_price": last_sold_price,
            "date_sold": date_sold[:100],
            "price_per_sqft": price_per_sqft,
            "zestimate": parse_int(prop.get("zestimate") or base.get("zestimate")),
            "rent_zestimate": parse_int(prop.get("rentZestimate") or base.get("rent_zestimate")),
            "hoa_fee": hoa_fee,
            "property_tax_rate": prop_tax_rate,
            "tax_annual_amount": parse_int(reso.get("taxAnnualAmount")),
            "tax_assessed_value": parse_int(reso.get("taxAssessedValue") or base.get("tax_assessed_value")),
            "price_note": f"Sold on {date_sold}" if (listing_category == "SOLD" and date_sold) else listing_category,
            "price_history_json": json.dumps(prop.get("priceHistory") or []),
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "full_bathrooms": full_bathrooms,
            "half_bathrooms": half_bathrooms,
            "sqft": sqft_val,
            "total_interior_livable_area": str(total_livable_area)[:100],
            "year_built": parse_int(prop.get("yearBuilt") or reso.get("yearBuilt")),
            "lot_size": lot_size_str[:100],
            "parcel_number": str(reso.get("parcelNumber") or prop.get("parcelId") or "")[:100],
            "room_types": room_types,
            "flooring": flooring[:255],
            "appliances": appliances,
            "heating": heating[:255],
            "cooling": cooling[:255],
            "laundry": laundry[:255],
            "parking": parking[:255],
            "exterior_features": exterior_features,
            "interior_features": interior_text,
            "amenities_json": json.dumps(
                {
                    "associationAmenities": reso.get("associationAmenities"),
                    "communityFeatures": reso.get("communityFeatures"),
                    "atAGlanceFacts": reso.get("atAGlanceFacts"),
                }
            ),
            "home_insights": extract_insight_phrases(prop.get("homeInsights")),
            "facts_and_features": json.dumps(
                {
                    "homeStatus": home_status,
                    "price": price_val,
                    "lastSoldPrice": last_sold_price,
                    "dateSold": date_sold,
                    "hoaFee": hoa_fee,
                    "propertyTaxRate": prop_tax_rate,
                    "resoFacts": clean_reso,
                    "taxHistory": prop.get("taxHistory"),
                }
            ),
            "walk_score": None,
            "transit_score": None,
            "bike_score": None,
            "neighborhood_details_json": json.dumps(
                {
                    "nearbyCities": prop.get("nearbyCities"),
                    "nearbyZipcodes": prop.get("nearbyZipcodes"),
                    "nearbyHomes": prop.get("nearbyHomes"),
                }
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
                }
            ),
            "latitude": str(prop.get("latitude") or base.get("latitude") or "")[:50],
            "longitude": str(prop.get("longitude") or base.get("longitude") or "")[:50],
            "days_on_zillow": parse_int(prop.get("daysOnZillow") or base.get("days_on_zillow")),
            "time_on_zillow": str(prop.get("timeOnZillow") or base.get("time_on_zillow") or "")[:100],
            "on_market_date": str(reso.get("onMarketDate") or "")[:100],
            "agent_name": str(attr_info.get("agentName") or "")[:255],
            "brokerage_name": str(prop.get("brokerageName") or attr_info.get("brokerName") or "")[:255],
            "contact_phone": str(attr_info.get("agentPhoneNumber") or attr_info.get("brokerPhoneNumber") or "")[:100],
            "mls_id": str(attr_info.get("mlsId") or "")[:100],
            "mls_name": str(attr_info.get("mlsName") or "")[:150],
            "provider_listing_id": str(prop.get("providerListingID") or base.get("provider_listing_id") or "")[:100],
            "schools_json": json.dumps(prop.get("schools") or []),
            "description": str(prop.get("description") or ""),
            "scraped_at": now_str,
        }


def get_existing_urls_for_state(
    ncb_client: NoCodeBackendClient, table: str, state_upper: str
) -> Tuple[Dict[str, Any], Set[str], int]:
    """Fetches existing listing URLs, ZPIDs, and exact SQL row count for a single state."""
    existing: Dict[str, Any] = {}
    zpids: Set[str] = set()
    exact_count = 0
    try:
        cnt_res = ncb_client.call_mcp_tool(
            "execute_sql",
            {
                "database": ncb_client.instance,
                "sql": f"SELECT COUNT(*) as cnt FROM {table} WHERE state = '{state_upper}';",
            },
        )
        for item in cnt_res.get("result", {}).get("content", []):
            txt = item.get("text", "")
            if "cnt" in txt:
                parsed = json.loads(txt)
                for res_block in parsed.get("results") or []:
                    for r in res_block.get("rows") or []:
                        if isinstance(r, dict) and r.get("cnt") is not None:
                            exact_count = int(r["cnt"])

        res = ncb_client.call_mcp_tool(
            "execute_sql",
            {
                "database": ncb_client.instance,
                "sql": f"SELECT id, zpid, listing_url FROM {table} WHERE state = '{state_upper}' LIMIT 25000;",
            },
        )
        for item in res.get("result", {}).get("content", []):
            txt = item.get("text", "")
            if "listing_url" in txt:
                parsed = json.loads(txt)
                for res_block in parsed.get("results") or []:
                    for r in res_block.get("rows") or []:
                        if isinstance(r, dict):
                            if r.get("listing_url"):
                                existing[r["listing_url"]] = r.get("id")
                            if r.get("zpid"):
                                zpids.add(str(r["zpid"]))
    except Exception as exc:
        print(f"  [!] Warning checking state {state_upper} existing URLs: {exc}")
    return existing, zpids, max(exact_count, len(existing), len(zpids))


def insert_state_records_fast(
    ncb_client: NoCodeBackendClient, table: str, records: List[Dict[str, Any]], batch_size: int = 400
) -> int:
    """High-throughput batch insertion into NoCodeBackend using 400-row batches."""
    inserted = 0
    for idx in range(0, len(records), batch_size):
        batch = records[idx : idx + batch_size]
        for attempt in range(3):
            try:
                res = ncb_client.call_mcp_tool(
                    "insert_mock_data",
                    {
                        "database": ncb_client.instance,
                        "table": table,
                        "records": batch,
                    },
                )
                if not res.get("result", {}).get("isError", False):
                    inserted += len(batch)
                    break
            except Exception as exc:
                if attempt == 2:
                    print(f"  [!] Batch insert failed after 3 attempts: {exc}")
                else:
                    time.sleep(1.0)
    return inserted


def build_sales_search_urls(
    states: List[str], categories: List[str], pages_per_state: int = 1
) -> List[tuple[str, str, str]]:
    """Returns (state_code, category, url) tuples for For Sale and/or Sold listings."""
    urls: List[tuple[str, str, str]] = []
    for st in states:
        st_clean = st.lower().strip()
        for page in range(1, pages_per_state + 1):
            suffix = f"{page}_p/" if page > 1 else ""
            if "for_sale" in categories:
                urls.append((st_clean.upper(), "FOR_SALE", f"https://www.zillow.com/{st_clean}/{suffix}"))
            if "sold" in categories:
                urls.append((st_clean.upper(), "SOLD", f"https://www.zillow.com/{st_clean}/sold/{suffix}"))
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape Zillow For Sale & Recently Sold listings into NoCodeBackend (43023_ziplow_db.sales)."
    )
    parser.add_argument(
        "--nationwide",
        action="store_true",
        help="Scrape For Sale and Sold listings across all 50 US states + DC.",
    )
    parser.add_argument(
        "--states",
        nargs="*",
        default=["co"],
        help="State codes to scrape (e.g. `--states co tx ca` or `--states all`). Default: co.",
    )
    parser.add_argument(
        "--categories",
        nargs="*",
        choices=["for_sale", "sold"],
        default=["for_sale", "sold"],
        help="Listing categories to scrape: `for_sale`, `sold`, or both (default: both).",
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
        help="Maximum listings to collect per state (default: 25, supports 10000+).",
    )
    parser.add_argument(
        "--max-listings",
        type=int,
        default=1000000,
        help="Maximum total listings to scrape across the run (default: 1,000,000).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: extract all search card / mapResults + hdpData + photo links without fetching each detail page.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update already-stored listings in 43023_ziplow_db.sales.",
    )
    parser.add_argument(
        "--table",
        default=SALES_TABLE,
        help="Target NoCodeBackend table (`sales` or `rentals`, default: `sales`).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.02,
        help="Delay in seconds between HTTP requests (default: 0.02).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel state workers for high-volume nationwide scraping (default: 4).",
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

    use_high_volume = args.per_state > 500

    print("============================================================")
    print("🏡  Zillow For Sale & Recently Sold Scraper -> NoCodeBackend")
    print(f"📌 Database Instance: {NCB_INSTANCE} (Table: {args.table})")
    print(f"🏷️  Categories: {', '.join(c.upper() for c in args.categories)}")
    print(f"🌎 Target States ({len(target_states)}): {', '.join(s.upper() for s in target_states)}")
    print(
        f"⚡ Mode: {'HIGH-VOLUME GRAPHQL 1000/CALL ENGINE (' + str(args.per_state) + '/state, workers=' + str(args.workers) + ')' if use_high_volume else ('FAST (Search Cards)' if args.fast else 'FULL DETAIL')}"
    )
    print("============================================================\n")

    ncb_client = NoCodeBackendClient(
        instance=NCB_INSTANCE,
        secret_key=NCB_SECRET_KEY,
        mcp_key=NCB_MCP_KEY,
        mcp_url=NCB_MCP_URL,
        data_api_url=NCB_DATA_API_URL,
        table=args.table,
    )
    scraper = ZillowSalesScraper(delay_seconds=args.delay)
    if use_high_volume:
        scraper.get_graphql_csrf()

    total_scraped = 0
    total_inserted = 0
    total_updated = 0

    if use_high_volume and len(target_states) > 1:
        def process_one_state(st_code: str) -> Tuple[str, int, int, int]:
            st_upper = st_code.upper()
            st_scraper = ZillowSalesScraper(delay_seconds=args.delay)
            if args.dry_run:
                st_existing, st_zpids, already_cnt = {}, set(), 0
            else:
                st_existing, st_zpids, already_cnt = get_existing_urls_for_state(ncb_client, args.table, st_upper)
            st_seen: Set[str] = set() if args.update_existing else set(st_existing.keys())
            if args.update_existing:
                st_zpids = set()
                already_cnt = 0
            needed_cnt = max(0, args.per_state - already_cnt)
            if needed_cnt == 0:
                print(f"✅ [{st_upper}] Already has {already_cnt} listings in {args.table}", flush=True)
                return (st_upper, 0, 0, already_cnt)
            print(
                f"🚀 [{st_upper}] Gathering {needed_cnt} additional listings (existing: {already_cnt}, target: {args.per_state})...",
                flush=True,
            )
            recs = st_scraper.gather_high_volume_state(
                state_code=st_code,
                target_count=needed_cnt,
                seen_urls=st_seen,
                categories=args.categories,
                delay=args.delay,
                seen_zpids=st_zpids,
            )
            ins_cnt = 0
            if recs and not args.dry_run:
                ins_cnt = insert_state_records_fast(ncb_client, args.table, recs, batch_size=500)
            print(
                f"   ✅ [{st_upper}] Synced ({args.table}) -> Inserted: {ins_cnt} (State Total: {already_cnt + ins_cnt})",
                flush=True,
            )
            return (st_upper, len(recs), ins_cnt, already_cnt + ins_cnt)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_one_state, st): st for st in target_states}
            for fut in as_completed(futures):
                try:
                    _, sc_cnt, in_cnt, _ = fut.result()
                    total_scraped += sc_cnt
                    total_inserted += in_cnt
                except Exception as exc:
                    print(f"  [!] Worker error for {futures[fut]}: {exc}", flush=True)

        print("\n============================================================")
        print(f"🎉 For Sale & Sold Scrape Complete! Total Scraped: {total_scraped}")
        if not args.dry_run:
            print(f"🗄️  Database ({NCB_INSTANCE}.{args.table}) -> Inserted: {total_inserted}, Updated: {total_updated}")
        print("============================================================")
        return

    for state_code in target_states:
        if total_scraped >= args.max_listings:
            break

        state_upper = state_code.upper()
        if args.dry_run:
            state_existing, state_zpids, already_count = {}, set(), 0
        else:
            state_existing, state_zpids, already_count = get_existing_urls_for_state(ncb_client, args.table, state_upper)
        seen_urls: Set[str] = set() if args.update_existing else set(state_existing.keys())
        if args.update_existing:
            state_zpids = set()
            already_count = 0

        if use_high_volume:
            needed = max(0, args.per_state - already_count)
            if needed == 0:
                print(f"✅ {state_upper} already has {already_count} listings in {args.table} (target: {args.per_state})")
                continue
            print(f"🚀 [{state_upper}] Gathering {needed} additional listings (existing: {already_count}, target: {args.per_state})...")
            state_records = scraper.gather_high_volume_state(
                state_code=state_code,
                target_count=needed,
                seen_urls=seen_urls,
                categories=args.categories,
                delay=args.delay,
                seen_zpids=state_zpids,
            )
            total_scraped += len(state_records)
            if state_records and not args.dry_run:
                ins = insert_state_records_fast(ncb_client, args.table, state_records, batch_size=500)
                total_inserted += ins
                print(
                    f"   ✅ {state_upper} Synced ({args.table}) -> Inserted: {ins} "
                    f"(State Total: {already_count + ins}, Run Inserted: {total_inserted})"
                )
            continue

        search_targets = build_sales_search_urls(
            [state_code], categories=args.categories, pages_per_state=args.pages_per_state
        )

        state_records = []
        cat_counts: Dict[str, int] = {}
        for _, cat_label, s_url in search_targets:
            if cat_counts.get(cat_label, 0) >= args.per_state or total_scraped >= args.max_listings:
                continue
            cards = scraper.extract_search_results(s_url)
            time.sleep(args.delay)

            for card in cards:
                if cat_counts.get(cat_label, 0) >= args.per_state or total_scraped >= args.max_listings:
                    break
                base_rec = scraper.parse_sale_search_card(card, category=cat_label, default_state=state_upper)
                detail_url = base_rec.get("listing_url", "")
                if not detail_url or detail_url in seen_urls:
                    continue

                seen_urls.add(detail_url)
                if args.fast:
                    enriched = base_rec
                else:
                    print(
                        f"🔎 [{state_upper} {cat_label} {cat_counts.get(cat_label, 0) + 1}/{args.per_state}] Enriching: {detail_url}"
                    )
                    try:
                        enriched = scraper.enrich_sale_detail_url(detail_url, base_record=base_rec) or base_rec
                    except Exception as exc:
                        print(f"   [!] Detail parse fallback for {detail_url}: {exc}")
                        enriched = base_rec
                    print(
                        f"   ✓ [{enriched['listing_category']}] {enriched['title']} | {enriched['price_display']} | "
                        f"{enriched['bedrooms']} bd / {enriched['bathrooms']} ba / {enriched['sqft']} sqft | "
                        f"📸 {enriched['photo_count']} photos"
                    )
                    time.sleep(args.delay)

                state_records.append(enriched)
                cat_counts[cat_label] = cat_counts.get(cat_label, 0) + 1
                total_scraped += 1

        if state_records:
            if args.dry_run:
                print(f"   [Dry Run] Collected {len(state_records)} sale/sold records for {state_upper}")
            else:
                res = ncb_client.upsert_records(
                    state_records,
                    existing_cache=state_existing,
                    update_existing=args.update_existing,
                )
                total_inserted += res["inserted"]
                total_updated += res["updated"]
                print(
                    f"   ✅ {state_upper} Synced ({args.table}) -> Inserted: {res['inserted']}, "
                    f"Updated: {res['updated']} (Session Synced: {total_inserted + total_updated})"
                )

    print("\n============================================================")
    print(f"🎉 For Sale & Sold Scrape Complete! Total Scraped: {total_scraped}")
    if not args.dry_run:
        print(f"🗄️  Database ({NCB_INSTANCE}.{args.table}) -> Inserted: {total_inserted}, Updated: {total_updated}")
    print("============================================================")


if __name__ == "__main__":
    main()
