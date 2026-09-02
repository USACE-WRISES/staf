---
title: Launch the Apps
nav_order: 7
description: "Launch the STAF web applications."
---
{% include staf_page_chrome.html %}

## Web Applications

{% include apps_hub.html %}

<p class="tools-engines-note">Every app computes watershed metrics with one of two engines, chosen by the framework rather than the user: the StreamCat lookup engine on the NHDPlus V2 network and the STAF site engine for the exact watershed at a point on any NHD stream. <a href="{{ site.baseurl }}/computation-engines/">Computation Engines</a> defines both, which tier uses which, and how to read the source labels in each report.</p>

## Downloads and resources

<ul class="tools-resources">
  <li><button type="button" class="tools-resource-link" data-metric-toolbox-download>Metric Toolbox (Excel)</button> <span class="tools-resource-note">The full STAF metric library as a spreadsheet.</span></li>
  <li><a class="tools-resource-link" href="{{ '/assets/docs/STAF_Factsheet.pdf' | relative_url }}">STAF Factsheet (PDF)</a> <span class="tools-resource-note">Overview of the framework.</span></li>
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
