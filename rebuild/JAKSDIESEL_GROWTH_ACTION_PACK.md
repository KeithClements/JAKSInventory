# JAK's Diesel — Storefront Growth Action Pack (Owner-Executed Items)

_Generated 2026-07-02. Companion to the deep-research market review. These are the items that require **your** account access (installing Shopify apps, creating Google accounts, ad billing). Everything here is copy-paste ready. The code items (JSON-LD, cross-reference/fitment blocks, trust badges, tag hygiene) are being built separately and will be staged for your review._

Store: `uyuedd-gc.myshopify.com` → jaksdiesel.com · Phone: (720) 445-6249

> ℹ️ **Store note (resolved):** `jaks-diesel-3.myshopify.com` and `uyuedd-gc.myshopify.com` are two `.myshopify.com` handles for the **same** store (primary domain www.jaksdiesel.com). There is **no store mismatch** — an earlier draft of this doc claimed one; that was wrong. The ERP is pointed at a valid domain. The real, verified gaps are the **data** ones below: 0 reviews, warranty metafield 0%, cross-references on ~35% of products.

---

## 1. Reviews — Judge.me is INSTALLED but has ZERO reviews (⚡ do this first, biggest single win)

**Confirmed live:** Judge.me is already installed (its metafields are on your products) but `review_widget_data` shows `number_of_reviews: 0` across the catalog. So the app is done — **the job is collecting reviews, not installing.** 0 reviews is the #1 reason a first-time visitor bounces on a $500–$12,000 part. Every major competitor (HHP, ATL, Thoroughbred, XDP) shows thousands. This is a trust wall.

**Turn it on properly + backfill (10 min):**
1. Shopify Admin → **Apps** → **Judge.me** (already there). If it's on the free plan, that's fine to start.
2. In Judge.me → **Settings**:
   - **Auto-publish** reviews 4★ and up; hold 1–3★ for a reply first.
   - Turn ON **photo & video reviews** (huge for parts — buyers want to see the real item).
   - Turn ON the **verified-buyer badge**.
   - Turn ON **star ratings on collection pages** (widgets → "review stars on listing").
3. **Backfill (this is where the reviews come from fast):** Judge.me → **Review requests** → **Send to past orders**. Send to the last **90 days** of fulfilled orders. Even a 5–10% response rate on your existing customers seeds the store with real reviews in a week.

> ⚠️ **Coordinate with me on schema:** Judge.me auto-injects `AggregateRating` structured data. When I add Product JSON-LD to the theme, I'll leave the rating out so we don't emit it twice (double rating schema = Google ignores both). Tell me once Judge.me is live.

**Review-request email (paste into Judge.me → email template):**

> **Subject:** How's the {{ product_title }} treating your truck?
>
> Hey {{ customer_first_name }},
>
> Thanks again for ordering from JAK's Diesel. If your **{{ product_title }}** is installed and running, would you take 30 seconds to leave a quick review? It genuinely helps other diesel guys buy with confidence — and photos of the part on your rig are gold.
>
> ⭐ **[Leave a review]({{ review_link }})**
>
> Ran into anything? Reply to this email or call us at **(720) 445-6249** — we'll make it right.
>
> — The JAK's Diesel crew

**SMS version (if you enable Judge.me SMS or send manually):**
> JAK's Diesel: How'd the {{ product }} work out? 30-sec review helps other diesel owners → {{ link }}. Questions? Call (720) 445-6249.

---

## 2. Abandoned-cart recovery (⚡ recovers demand you already paid to get)

**Why:** Per Klaviyo's benchmark data, cart-recovery is the single highest-ROI automation in e-commerce, and automotive brands are on the strong end of it. At your traffic level, every recoverable cart matters.

**Fastest path (free, 15 min) — Shopify built-in:**
1. Shopify Admin → **Marketing** → **Automations** → **Create automation** → **Abandoned checkout**.
2. Turn it on. Then add two more touches manually (Shopify lets you clone/schedule): a 1-hour, a 24-hour, and a 72-hour email.

**Better path (when ready) — Klaviyo:** free up to 250 contacts / 500 emills, far better segmentation + SMS. Install "Klaviyo: Email Marketing & SMS" from the App Store, connect, and it auto-builds an Abandoned Cart flow you paste the copy below into.

**3-email sequence copy:**

**Email 1 — send +1 hour · Subject: "Still thinking it over?"**
> You left the **{{ product }}** in your cart. Not sure it fits your setup? That's the #1 thing we help with — reply or call **(720) 445-6249** and we'll confirm fitment for your exact truck (year / engine) before you buy. Your cart's saved: **[Finish checkout]({{ url }})**

**Email 2 — send +24 hours · Subject: "Your {{ product }} is still in stock"**
> Quick heads-up — the **{{ product }}** you were looking at is in stock and ships **same day**. Backed by our warranty and real diesel techs on the phone, not a call center. **[Grab it before it's gone]({{ url }})** · Questions? (720) 445-6249

**Email 3 — send +72 hours · Subject: "Last call + here's 5% off"** _(optional discount)_
> Still on the fence? Here's **5% off** to close it out: code **DIESEL5** at checkout. Same-day shipping, warranty included, fitment help a phone call away. **[Complete your order]({{ url }})**

**SMS (Klaviyo, +4 hours, requires opt-in):**
> JAK's Diesel: your {{ product }} is saved & in stock, ships same day. Need fitment confirmed? Text us your truck's year+engine. Finish: {{ url }}

> 💡 The fitment-help angle is your differentiator — competitors dump you into a self-serve checkout; you have real techs. Lead with that in every recovery message.

---

## 3. Google Business Profile (local pack + more reviews)

**The catch:** pure online-only sellers normally can't hold a GBP. **The workaround:** if JAK's has any physical footprint (shop, warehouse, or even a home-office service address in the Denver metro — your (720) number suggests CO), register as a **service-area business** and hide the street address.

**Steps:**
1. Go to **business.google.com** → **Manage now** → enter "JAK's Diesel".
2. Choose category **"Auto parts store"** (primary) + add **"Truck parts supplier"** and **"Diesel engine repair service"** if you do any install/consult.
3. When asked "Do you serve customers at your location?" → **No, I deliver / serve them** → set your **service area** (Denver metro + "United States" for shipping).
4. Verify (postcard or phone).
5. Fill it fully: hours, phone **(720) 445-6249**, website, and the description below. Post the products you push online.
6. **Ask for Google reviews** — same customers you email for Judge.me reviews, ask for a Google review too. Local reviews feed the map pack and Google's trust signals.

**Business description (paste):**
> JAK's Diesel supplies performance and replacement diesel engine parts for Duramax, Cummins, and Power Stroke trucks — turbochargers, cylinder heads, fuel injectors, EGR/DPF components, and complete engine overhaul kits. Same-day shipping, real diesel-tech fitment support, and a parts warranty on every order. Call (720) 445-6249 to confirm fitment for your truck.

---

## 4. Google Shopping / Performance Max (⏳ set up now, launch AFTER cross-reference data is in the feed)

**Why the wait:** for auto parts, the product feed IS the campaign. Launching before fitment + part numbers are in your titles wastes spend on wrong-fit clicks. I'm adding that data to the storefront/feed first — then this converts.

**Set up the plumbing now:**
1. Shopify Admin → **Apps** → install **"Google & YouTube"** channel → connect a **Google Merchant Center** account (create one free at merchantcenter.google.com).
2. Let it sync your catalog. Fix any feed disapprovals it flags (usually GTIN/MPN or "misrepresentation" on delete/EGR parts — handle those carefully; Google restricts emissions-defeat items, so keep those out of the paid feed).
3. Ensure every product feeds **MPN** and **brand** (GTIN where it exists). This rides on the same cross-reference work I'm doing.

**When you launch:**
- Start with a **Standard Shopping** campaign (more control than PMax) segmented by margin using **custom labels** (label products with your margin tier so you can bid up the good stuff).
- Put **fitment in the product title** (year + engine + part type) — that's what wins auto-parts Shopping.
- Split **branded vs. non-branded**; branded (people searching "JAK's Diesel") is cheap and should always be on.
- Budget small ($10–20/day), watch search-term report, add negative keywords weekly.

---

## 5. Measurement (so we can prove what's working)

- **Google Search Console** — verify jaksdiesel.com (DNS or the Shopify meta-tag method). This is non-negotiable; it's how we'll see impressions/clicks by query and confirm the cross-reference pages get indexed. Free.
- **GA4** — confirm it's installed (Shopify → Online Store → Preferences, or via Google & YouTube app). Watch: sessions, add-to-cart rate, conversion rate.
- Baseline to beat (today): ~15 sessions/day, 0 reviews, conversion unknown. Recheck in 30 / 60 / 90 days.

---

## Priority order for you

| Do it | Item | Time | Cost |
|---|---|---|---|
| **Today** | Judge.me install + 90-day backfill | 15 min | Free |
| **Today** | Shopify abandoned-checkout automation ON | 15 min | Free |
| **This week** | Google Business Profile + verify | 20 min + postcard wait | Free |
| **This week** | Google Search Console verify | 10 min | Free |
| **After cross-ref ships** | Merchant Center + Shopping | 1 hr | Ad budget |

Tell me when Judge.me is live and I'll wire the theme's Product JSON-LD to hand off ratings to it cleanly.
