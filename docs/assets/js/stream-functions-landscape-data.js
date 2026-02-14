(() => {
  // Hotspot and callout coordinates are calibrated from manual placement on LandscapeFunctions.png.
  window.STAF_STREAM_FUNCTIONS_LANDSCAPE_DATA = [
    {
      id: 'catchment-hydrology',
      name: 'Catchment Hydrology',
      category: 'hydrology',
      description:
        'Represents watershed-scale runoff and infiltration processes that control how water and pollutants enter stream networks.',
      hotspot: { xPct: 43.7, yPct: 36.24 },
      callout: { xPct: 35.47, yPct: 20.9 }
    },
    {
      id: 'surface-water-storage',
      name: 'Surface Water Storage',
      category: 'hydrology',
      description:
        'Reflects wetlands, ponds, and floodplain storage features that attenuate peaks and sustain baseflow conditions.',
      hotspot: { xPct: 21.06, yPct: 48.83 },
      callout: { xPct: 22.4, yPct: 30.37 }
    },
    {
      id: 'reach-inflow',
      name: 'Reach Inflow',
      category: 'hydrology',
      description:
        'Captures tributary and local inflows that influence discharge, pollutant loading, and in-channel conditions.',
      hotspot: { xPct: 43.1, yPct: 56.93 },
      callout: { xPct: 29.95, yPct: 27.81 }
    },
    {
      id: 'streamflow-regime',
      name: 'Streamflow Regime',
      category: 'hydrology',
      description:
        'Describes flow timing, magnitude, and duration patterns that organize physical habitat and ecological function.',
      hotspot: { xPct: 43.55, yPct: 49.04 },
      callout: { xPct: 37.06, yPct: 32.62 }
    },
    {
      id: 'high-flow-dynamics',
      name: 'High Flow Dynamics',
      category: 'hydraulics',
      description:
        'Represents the energy of high flows that mobilize sediment, maintain channel form, and connect floodplains.',
      hotspot: { xPct: 25.13, yPct: 55.87 },
      callout: { xPct: 12.1, yPct: 59.06 }
    },
    {
      id: 'baseflow-low-flow-dynamics',
      name: 'Baseflow and Low Flow Dynamics',
      category: 'hydraulics',
      description:
        'Evaluates low-flow behavior that supports wetted habitat continuity, temperature buffering, and dry-season resilience.',
      hotspot: { xPct: 31.47, yPct: 59.92 },
      callout: { xPct: 18.79, yPct: 67.17 }
    },
    {
      id: 'floodplain-connectivity',
      name: 'Floodplain Connectivity',
      category: 'hydraulics',
      description:
        'Assesses how frequently and effectively stream channels connect laterally to floodplains for exchange processes.',
      hotspot: { xPct: 42.34, yPct: 74.86 },
      callout: { xPct: 28.03, yPct: 74.08 }
    },
    {
      id: 'hyporheic-connectivity',
      name: 'Hyporheic Connectivity',
      category: 'hydraulics',
      description:
        'Tracks exchange between channel water and shallow subsurface flow paths that influence chemistry and habitat.',
      hotspot: { xPct: 42.02, yPct: 81.94 },
      callout: { xPct: 32.18, yPct: 83.69 }
    },
    {
      id: 'channel-evolution',
      name: 'Channel Evolution',
      category: 'geomorphology',
      description:
        'Represents long-term channel adjustment trajectories driven by legacy impacts, altered flows, and sediment imbalance.',
      hotspot: { xPct: 48.68, yPct: 58.43 },
      callout: { xPct: 47.37, yPct: 22.86 }
    },
    {
      id: 'sediment-continuity',
      name: 'Sediment Continuity',
      category: 'geomorphology',
      description:
        'Reflects whether sediment supply, transport, and storage are balanced through the reach and connected network.',
      hotspot: { xPct: 49.29, yPct: 47.12 },
      callout: { xPct: 56.29, yPct: 24.06 }
    },
    {
      id: 'channel-floodplain-dynamics',
      name: 'Channel and Floodplain Dynamics',
      category: 'geomorphology',
      description:
        'Captures migration, bar development, and floodplain-building processes that create and maintain channel complexity.',
      hotspot: { xPct: 44.61, yPct: 82.32 },
      callout: { xPct: 42.16, yPct: 93.6 }
    },
    {
      id: 'bed-composition-bedform-diversity',
      name: 'Bed Composition and Bedform Diversity',
      category: 'geomorphology',
      description:
        'Evaluates substrate composition and bedform heterogeneity that structure hydraulics, habitat quality, and process rates.',
      hotspot: { xPct: 53.36, yPct: 88.94 },
      callout: { xPct: 59.16, yPct: 93.15 }
    },
    {
      id: 'carbon-processing',
      name: 'Carbon Processing',
      category: 'physicochemistry',
      description:
        'Represents transformation and storage of carbon across channel, floodplain, and biologically active environments.',
      hotspot: { xPct: 72.68, yPct: 44.77 },
      callout: { xPct: 84.87, yPct: 29.47 }
    },
    {
      id: 'light-thermal-regime',
      name: 'Light and Thermal Regime',
      category: 'physicochemistry',
      description:
        'Describes light exposure and temperature conditions that regulate metabolism, habitat suitability, and stress response.',
      hotspot: { xPct: 73.44, yPct: 47.33 },
      callout: { xPct: 90.5, yPct: 47.19 }
    },
    {
      id: 'nutrient-cycling',
      name: 'Nutrient Cycling',
      category: 'physicochemistry',
      description:
        'Tracks nutrient retention, uptake, transformation, and release across hydrologic, geomorphic, and biological pathways.',
      hotspot: { xPct: 70.27, yPct: 54.8 },
      callout: { xPct: 86.35, yPct: 58.15 }
    },
    {
      id: 'water-soil-quality',
      name: 'Water and Soil Quality',
      category: 'physicochemistry',
      description:
        'Reflects chemical conditions in water and soils that affect biota, process rates, and ecosystem resilience.',
      hotspot: { xPct: 66.49, yPct: 59.07 },
      callout: { xPct: 78.7, yPct: 64.91 }
    },
    {
      id: 'community-dynamics',
      name: 'Community Dynamics',
      category: 'biology',
      description:
        'Represents compositional and interaction patterns among organisms responding to habitat, disturbance, and connectivity.',
      hotspot: { xPct: 61.51, yPct: 44.99 },
      callout: { xPct: 65.85, yPct: 29.47 }
    },
    {
      id: 'watershed-connectivity',
      name: 'Watershed Connectivity',
      category: 'biology',
      description:
        'Captures movement pathways across stream networks that support dispersal, recolonization, and life-cycle completion.',
      hotspot: { xPct: 66.95, yPct: 38.59 },
      callout: { xPct: 72.97, yPct: 26.01 }
    },
    {
      id: 'habitat-provision',
      name: 'Habitat Provision',
      category: 'biology',
      description:
        'Evaluates the availability and diversity of physical habitat features needed by aquatic and riparian organisms.',
      hotspot: { xPct: 62.57, yPct: 63.55 },
      callout: { xPct: 70.95, yPct: 71.22 }
    },
    {
      id: 'population-support',
      name: 'Population Support',
      category: 'biology',
      description:
        'Represents whether habitat and process conditions sustain viable populations through growth, survival, and reproduction.',
      hotspot: { xPct: 55.32, yPct: 85.74 },
      callout: { xPct: 65.21, yPct: 79.18 }
    }
  ];
})();
