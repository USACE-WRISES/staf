# StreamCurves Brainstorming

## Dataset-Building Workflow

- **Streamline dataset development**
  - Simplify the current process for assembling the reference-curve dataset.
  - Reduce unnecessary manual processing.
  - Clearly separate required inputs, automated steps, review points, and final outputs.
  - Preserve enough intermediate information to review and revise the dataset later.
  - Prepare the workflow for greater automation.

## Reference-Condition Screening

- **Add a final site-screening step**
  - Run the screening after the initial candidate dataset has been assembled.
  - Send candidate site coordinates and identifiers to the Easy batch processor.
  - Retrieve the Easy assessment results for every candidate site.
  - Apply the selected criteria for identifying functional or reference-condition sites.
  - Automatically retain only qualifying sites for reference-curve development.
  - Preserve:
    - Sites that passed.
    - Sites that failed.
    - Easy results for each site.
    - The criteria used.
    - The reason each site was included or excluded.
  - Allow the screening criteria or individual site decisions to be reviewed and revised.

## Reference-Curve Analysis

- **Streamline regional reference-curve development**
  - Simplify the analysis workflow used to generate regional reference curves.
  - Focus initial development on **Level III ecoregions**.
  - Automate data preparation, grouping, statistical analysis, curve fitting, diagnostics, and output generation where practical.
  - Include review or decision points when automated selection is not appropriate.
  - Allow the analysis to be rerun after changing assumptions, screening criteria, or model settings.

- **Run an initial ecoregion pilot**
  - Select one Level III ecoregion.
  - Test the complete process from raw candidate data through final preliminary reference curves.
  - Document failures, manual interventions, timing, and areas requiring additional automation.
  - Use the pilot to refine the workflow before expanding to other ecoregions.

## Preliminary Assessment Status and Validation

- **Label StreamCurves products as Preliminary**
  - Mark newly generated reference curves and assessments as **Preliminary**.
  - Keep the Preliminary designation until adequate field validation has been completed.
  - Display the designation clearly in the assessment metadata, reports, library records, and Deep.

- **Support field validation**
  - Track whether a reference curve has been field validated.
  - Record the validation sites, data, dates, findings, and revisions.
  - Update the assessment version when field-validation results change the curves or scoring approach.

- **Prepare validated assessments for EcoPCX certification**
  - After field validation, compile the assessment methods, datasets, reference curves, results, and supporting documentation.
  - Submit the completed assessment for EcoPCX certification.
  - Track its status as Preliminary, under review, certified, revised, or retired.

## Shared Assessment Library

- **Publish StreamCurves products to the shared library**
  - Add preliminary assessments and reference curves to the shared assessment library.
  - Include:
    - Assessment name.
    - Applicable Level III ecoregion.
    - Version.
    - Preliminary or certified status.
    - Creation and update dates.
    - Reference dataset.
    - Reference curves.
    - Required metrics.
    - Scoring information.
    - Validation and certification status.
  - Make preliminary assessments available for Deep to locate and use.
  - Clearly distinguish preliminary assessments from certified assessments.
