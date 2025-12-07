import React from 'react';

import Blog from '../../components/Blog';
import Container from '../../components/Container';
import Layout from '../../components/Layout/Layout';
import * as styles from './bafotech-ops.module.css';

const BafotechOpsPage = () => {
  return (
    <Layout>
      <div className={styles.root}>
        <Container>
          <div className={styles.blogContainer}>
            <Blog
              category={'data engineering'}
              title={'Bafotech — Manufacturing Operations Data Simulation + Cleaning + EDA'}
              image={'/blogFeatured.png'}
              alt={'Factory operations data engineering blog cover'}
            >
              <div className={styles.content}>
                <p className={styles.excerpt}>
                  A complete Python workflow to simulate a month of multi-machine production,
                  clean realistic data issues, and generate exploratory visuals for downstream
                  analysis and dashboards.
                </p>
                <p className={styles.blogParagraph}>
                  The script below spins up hourly sensor readings, shift-level throughput,
                  maintenance logs, rare failure events, and derived downtime summaries for a
                  fictional manufacturer, Bafotech. Data issues are deliberately injected—missing
                  technicians, inconsistent casing on categories, out-of-range sensor spikes, and
                  extreme throughput values—so the cleaning stage has something meaningful to
                  address.
                </p>
                <div className={styles.callouts}>
                  <h2 className={styles.blogSubHeader}>What the simulation produces</h2>
                  <ul className={styles.list}>
                    <li>Production, maintenance, sensor, failure, and downtime tables saved as CSVs.</li>
                    <li>Outlier-aware cleaning that normalizes machine IDs, product types, and sensor ranges.</li>
                    <li>
                      Exploratory plots covering throughput distributions, sensor correlations, downtime by machine,
                      and failure counts.
                    </li>
                  </ul>
                </div>
                <p className={styles.blogParagraph}>
                  The <code>simulate</code> function seeds the Faker generator, assembles baseline trends, and injects
                  anomalies across five machines. The <code>clean</code> function standardizes casing, caps unrealistic
                  telemetry, and converts stringly typed fields. Finally, <code>eda_plots</code> produces histograms,
                  scatter plots, a heatmap, a time series slice for M1, and downtime/failure summaries—handy scaffolding for a
                  quick EDA deck or demo dashboard.
                </p>
              </div>

              <div className={styles.codeBlock}>
                <pre>
                  {`"""
Bafotech — Manufacturing Operations Data Simulation + Cleaning + EDA
Author: (generated template)
Period simulated: 2023-01-01 to 2023-01-30 (hourly sensors; per-shift production)

Outputs:
- CSVs: production.csv, maintenance.csv, sensor.csv, failures.csv, downtime.csv
- Plots: throughput_hist.png, throughput_boxplot.png, sensor_load_vs_temp.png, sensor_temp_vs_vib.png,
         sensor_load_vs_vib.png, sensor_corr_heatmap.png, m1_sensor_timeseries.png, downtime_by_machine.png,
         failure_counts_by_type.png
"""

import numpy as np
import pandas as pd
from faker import Faker
import matplotlib.pyplot as plt
from pathlib import Path

# ... simulate(), clean(), eda_plots(), and main() definitions ...
`}
                </pre>
              </div>

              <div className={styles.content}>
                <p className={styles.blogParagraph}>
                  Drop the script into your analytics sandbox, run <code>python bafotech.py</code>, and you will get a fully
                  reproducible dataset plus PNGs ready for notebooks or presentations. Because the seed is fixed to 42, your
                  teammates can regenerate identical outputs when validating cleaning rules or testing modeling pipelines.
                </p>
              </div>
            </Blog>
          </div>
        </Container>
      </div>
    </Layout>
  );
};

export default BafotechOpsPage;
