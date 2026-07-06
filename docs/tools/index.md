---
title: Launch the Apps
nav_order: 7
description: "Launch the STAF web applications or download STAF Desktop."
---
{% include staf_page_chrome.html %}

## Web Applications

Each tier has its own app. All four use the same 20 stream functions and scoring, so results are comparable across tiers.

{% include apps_hub.html %}

## Desktop Versions

STAF Desktop runs the same four tools locally on Windows. Same interface, same results, no admin rights needed.

<ul class="tools-downloads">
  <li><a class="btn btn-primary" href="https://github.com/USACE-WRISES/staf/releases/latest/download/StafDesktop-win-Setup.exe">Download STAF Desktop</a> <span class="tools-download-note">Per-user install; keeps itself up to date.</span></li>
  <li><a class="btn" href="https://github.com/USACE-WRISES/staf/releases/latest/download/StafDesktop-win-Portable.zip">Portable version (zip)</a> <span class="tools-download-note">Extract anywhere and run <code>STAF Desktop.exe</code>.</span></li>
</ul>

Requires Windows 10/11 and an internet connection (assessments use live USGS/EPA data). The first launch downloads the runtime (about 310 MB); after that the apps start instantly and update automatically. If Windows shows a SmartScreen notice, choose <em>More info</em>, then <em>Run anyway</em>.

## Downloads and resources

<ul class="tools-downloads">
  <li><button type="button" class="btn btn-primary" data-metric-toolbox-download>Metric Toolbox (Excel)</button> <span class="tools-download-note">The full STAF metric library as a spreadsheet.</span></li>
  <li><a class="btn" href="{{ site.baseurl }}/assets/docs/STAF_Factsheet.pdf">STAF Factsheet (PDF)</a> <span class="tools-download-note">One-page overview of the framework.</span></li>
</ul>

## References
- Stepchinski, L. M., McKay, S. K., Harris, A. E., & Menichino, G. T. (2025). A Review of Stream Assessment Methods in the United States. JAWRA Journal of the American Water Resources Association, 61(6), e70056.
- Stepchinski, L. M., Menichino, G. T., & McKay, S. K. (2024, December). A Tiered Approach for Assessing Stream Ecosystem Condition. In AGU Fall Meeting Abstracts (Vol. 2024, No. 983, pp. H11X-0983).
- David, G. C., Stepchinski, L. M., Wiest, S. R., & Menichino, G. T. (In review). Stream Functions Assessment and Rapid Index (SFARI): A nationally applicable, rapid, function-based stream assessment. ERDC/EMRRP Technical Report. Vicksburg, MS: U.S. Army Engineer Research and Development Center.
- Alaska Stream Quantification Tool Steering Committee (Steering Committee). 2021. Stream Quantification Tool and Debit Calculator for the Alaskan Interior User Manual and Spreadsheets. Version 1.0. Salcha-Delta Soil and Water Conservation District, Delta Junction, AK.
- U.S. Army Corps of Engineers. 2020. Colorado Stream Quantification Tool (CSQT) User Manual and Spreadsheets. Version 1.0. U.S. Army Corps of Engineers, Albuquerque District, Pueblo Regulatory Office.
- Michigan Department of Environment, Great Lakes, and Energy (EGLE). 2020. Michigan Stream Quantification Tool: Spreadsheet User Manual, MiSQT v1.0., EGLE, Lansing, MI.
- Minnesota Stream Quantification Tool Steering Committee (MNSQT SC). 2020. Minnesota Stream Quantification Tool and Debit Calculator (MNSQT) User Manual, Version 2.0. U.S. Environmental Protection Agency, Office of Wetlands, Oceans and Watersheds (Contract # EPC-17-001), Washington, D.C.
- Harman, W.A. and C.J. Jones. 2017. North Carolina Stream Quantification Tool: Data Collection and Analysis Manual, NC SQT v3.0. Environmental Defense Fund, Raleigh, NC.
- South Carolina Steering Committee. 2022. South Carolina Stream Quantification Tool: Data Collection and Analysis Manual, SC SQT v1.1. South Carolina Department of Natural Resources, Columbia, SC.
- Wisconsin Stream Quantification Tool Steering Committee (WISQT SC). 2023. Stream Quantification Tool and Debit Calculator for Wisconsin User Manual and Workbooks. Beta Version.
- U.S. Army Corps of Engineers. 2023. Wyoming Stream Quantification Tool (WSQT) User Manual and Spreadsheet. Version 2.0, Omaha District, Wyoming Regulatory Office, Cheyenne Wyoming.

<script src="{{ '/assets/js/metric-toolbox.js' | relative_url }}" defer></script>
