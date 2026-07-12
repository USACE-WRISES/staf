"""Thin clients for each national data source.

Every client is responsible for: building the request, retry/backoff, caching
(HyRiver AsyncRetriever where applicable), and returning typed Python data.
Clients must surface failures as exceptions caught by the metric adapters
(which then degrade to 'unavailable') — they must not crash the orchestrator.

Planned modules (Phases 2-3):
  streamcat        EPA StreamCat REST (per-COMID watershed/riparian metrics)
  nwis_waterdata   USGS dataretrieval `waterdata` module (daily Q, gages)
  wqp              EPA Water Quality Portal (observed TN/TP/temp/turbidity/DO)
  attains          EPA ATTAINS gispub ArcGIS MapServer layer 3 (impairment)
  nas              USGS Nonindigenous Aquatic Species (invasives by HUC)
  nid_barriers     USACE NID FeatureServer + bundled NABD/FWS-SARP
  nwi              USFWS National Wetlands Inventory MapServer
  threedep         USGS 3DEP via py3dep (DEM, slope, transects)
  nlcd             NLCD via pygeohydro (fallback land cover/riparian)
  sda_soils        USDA Soil Data Access post.rest T-SQL (Kfactor/Ksat/HYDGRP)
  tiger_roads      Census TIGERweb roads (road-stream crossings)
  vaa              NHDPlus value-added attributes (drainage area, GNIS name)
  sparrow_lookup   bundled SPARROW TN/TP per-COMID parquet
  ecoregion        bundled EPA nutrient ecoregion polygons + reference criteria
  bankfull         bundled regional hydraulic-geometry coefficients (a*DA^b)
"""
