# Easy Brainstorming

## Batch Processing

- **Develop batch processing**
  - Allow the user to submit one or more point locations.
  - Assign or retain a unique site identifier for each point.
  - Run the complete Easy assessment for every submitted point.
  - Produce:
    - A simple, readable Easy report for each point.
    - A combined summary of all processed points.
    - A structured data output that other applications can easily consume.
  - Include assessment results, functional classifications, warnings, and any missing-data indicators in the output.

## StreamCurves Integration

- **Support StreamCurves reference-site screening**
  - Allow StreamCurves to submit a set of candidate reference-site locations to the Easy batch processor.
  - Return Easy results in a consistent, machine-readable format.
  - Use the Easy results to flag sites that meet the selected functional or reference-condition criteria.
  - Allow StreamCurves to automatically retain only the qualifying sites.
  - Preserve excluded sites and their exclusion reasons for review.
