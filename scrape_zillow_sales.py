#!/usr/bin/env python3
"""
Zillow For Sale & Recently Sold Scraper -> NoCodeBackend (NCB) Database (`43023_ziplow_db.sales`)

Scrapes For Sale (`FOR_SALE`) and Recently Sold (`RECENTLY_SOLD` / `SOLD`) listings across
Colorado or all 50 US states + DC from:
  - https://www.zillow.com/<state>/          (For Sale)
  - https://www.zillow.com/<state>/sold/     (Recently Sold)
  - Individual Zillow `/homedetails/...` pages

Extracts 75+ columns per property including:
  - All picture links (`image_url`, `photo_count`, and full array of high-res URLs in `photos_json`)
  - Sale price, last sold price, date sold, price per sqft, Zestimate, Rent Zestimate, HOA fee,
    property tax rate, annual tax amount, tax assessed value, and price history
  - Interior & exterior facts & features (bedrooms, bathrooms, full/half baths, sqft, lot size,
    year built, room types, flooring, appliances, heating, cooling, laundry, parking, exterior)
  - County, FIPS, neighborhood, Walk/Transit/Bike scores, assigned schools, MLS ID/name,
    listing agent/brokerage contact info, 3D virtual tour & floor plan links
"""

import argparse
import datetime
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin

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


class ZillowSalesScraper(ZillowColoradoScraper):
    """Scrapes Zillow For Sale and Recently Sold search pages and /homedetails/ pages."""

    def parse_sale_search_card(
        self, card: Dict[str, Any], category: str = "FOR_SALE", default_state: str = "CO"
    ) -> Dict[str, Any]:
        """Extracts all available fields and picture links from a For Sale or Sold search card."""
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

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        title = card.get("address") or card.get("statusText") or ""
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
            "home_insights": "",
            "facts_and_features": json.dumps(
                {
                    "hdpData": hdp,
                    "factsAndFeatures": ff,
                    "listCardRecommendation": recs,
                    "statusText": card.get("statusText"),
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
            "videos_and_media_json": json.dumps({"has3DModel": card.get("has3DModel")}),
            "latitude": str(lat_long.get("latitude") or hdp.get("latitude") or "")[:50],
            "longitude": str(lat_long.get("longitude") or hdp.get("longitude") or "")[:50],
            "days_on_zillow": parse_int(hdp.get("daysOnZillow")),
            "time_on_zillow": time_on_zillow_str[:100],
            "on_market_date": "",
            "agent_name": "",
            "brokerage_name": str(card.get("brokerName") or "")[:255],
            "contact_phone": "",
            "mls_id": "",
            "mls_name": "",
            "provider_listing_id": str(card.get("providerListingId") or "")[:100],
            "schools_json": "[]",
            "description": "",
            "scraped_at": now_str,
        }

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
        help="Maximum listings to collect per state per category (default: 25).",
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
        help="Fast mode: extract all search card + hdpData + carousel photo links without fetching each detail page.",
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
        default=0.3,
        help="Delay in seconds between HTTP requests (default: 0.3).",
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
    print("🏡  Zillow For Sale & Recently Sold Scraper -> NoCodeBackend")
    print(f"📌 Database Instance: {NCB_INSTANCE} (Table: {args.table})")
    print(f"🏷️  Categories: {', '.join(c.upper() for c in args.categories)}")
    print(f"🌎 Target States ({len(target_states)}): {', '.join(s.upper() for s in target_states)}")
    print(f"⚡ Mode: {'FAST (Search Cards + Carousel Photos)' if args.fast else 'FULL DETAIL (Search + Detail Pages)'}")
    print("============================================================\n")

    scraper = ZillowSalesScraper(delay_seconds=args.delay)
    ncb_client = NoCodeBackendClient(
        instance=NCB_INSTANCE,
        secret_key=NCB_SECRET_KEY,
        mcp_key=NCB_MCP_KEY,
        mcp_url=NCB_MCP_URL,
        data_api_url=NCB_DATA_API_URL,
        table=args.table,
    )
    existing_urls = {} if args.dry_run else ncb_client.get_existing_urls()
    seen_urls: Set[str] = set() if args.update_existing else set(existing_urls.keys())

    total_scraped = 0
    total_inserted = 0
    total_updated = 0

    for state_code in target_states:
        if total_scraped >= args.max_listings:
            break

        state_upper = state_code.upper()
        search_targets = build_sales_search_urls(
            [state_code], categories=args.categories, pages_per_state=args.pages_per_state
        )

        state_records: List[Dict[str, Any]] = []
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
                    existing_cache=existing_urls,
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
