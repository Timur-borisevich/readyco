# ReadyCo Market — Figma UI Kit

These SVG files are designed to be imported directly into Figma.

## Import
1. Open Figma.
2. Create a new Design file.
3. Drag the SVG files into the canvas, or use File → Import.
4. Each SVG is vector-based and can be ungrouped/edited.
5. Install/use Poppins for the intended typography.

## Files
01_Desktop_Home.svg — desktop homepage
02_Mobile_Home.svg — mobile homepage
03_Mobile_Listing_Detail.svg — mobile listing details
04_Mobile_Filters.svg — mobile filters
05_Submit_Offer.svg — seller submission form
06_Design_System.svg — colors, typography and core UI

## Recommended Figma pages
00 Cover
01 Design System
02 Desktop
03 Mobile
04 Components
05 Prototype
06 Assets

## Component strategy
Rebuild the imported visual pieces as native Figma components:
- Header
- Logo
- SearchBar
- CategoryBadge
- ListingCard
- StatusBadge
- Price
- FilterSheet
- RequestDetailsButton
- SubmitOfferForm
- MobileBottomNav

Use Auto Layout for all production components.

## Product architecture
Telegram is the content source:
Telegram Channel → Bot → AI Parser → Listing JSON → Database → Website.

The frontend should use Listing data objects so the same component renders every offer.
Never invent missing listing information.
