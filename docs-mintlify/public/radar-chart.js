// Radar Chart for Seqera vs Tracer comparison
// This script initializes the radar chart when the page loads

function initRadarChart() {
  const canvas = document.getElementById('seqera-tracer-radar-chart');
  if (!canvas) {
    console.log('Canvas element not found');
    return;
  }

  if (typeof Chart === 'undefined') {
    console.log('Chart.js not loaded yet, retrying...');
    setTimeout(initRadarChart, 100);
    return;
  }

  console.log('Initializing radar chart...');

  const isDark = document.documentElement.classList.contains('dark');
  const gridColor = isDark ? '#374151' : '#e5e7eb';
  const textColor = isDark ? '#ffffff' : '#374151';

  const data = {
    labels: [
      ['Workflow', 'Orchestration'],
      ['Task', 'Level', 'Visibility'],
      ['Framework', 'Support'],
      ['Kernel', 'Level', 'Observability'],
      ['Synthetic', 'Logging'],
      ['Cost', 'Performance', 'Optimization'],
      ['Anomaly', 'Detection'],
      ['Scientific', 'Workload', 'Suitability'],
      ['Observability', 'Depth']
    ],
    datasets: [
      {
        label: 'Seqera-ONLY',
        data: [4, 2, 1, 0, 0, 2, 1, 5, 2],
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.15)',
        fill: true,
        borderWidth: 2.5,
        pointBackgroundColor: '#3b82f6',
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointHoverBackgroundColor: '#fff',
        pointHoverBorderColor: '#3b82f6',
        pointHoverBorderWidth: 3,
        descriptions: [
          'Only Nextflow and Seqera Platform',
          'Basic metrics per task',
          'N/A, only Nextflow',
          'N/A',
          'N/A',
          'Manual via dashboard',
          'Limited detection capabilities',
          'Optimized for Nextflow workflows',
          'Task/runtime level'
        ]
      },
      {
        label: 'Seqera + Tracer',
        data: [5, 5, 5, 5, 5, 5, 5, 5, 5],
        borderColor: '#27BF9F',
        backgroundColor: 'rgba(39, 191, 159, 0.15)',
        fill: true,
        borderWidth: 2.5,
        pointBackgroundColor: '#27BF9F',
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointHoverBackgroundColor: '#fff',
        pointHoverBorderColor: '#27BF9F',
        pointHoverBorderWidth: 3,
        descriptions: [
          'Fully compatible',
          'Deep system-level telemetry',
          'Framework-agnostic',
          'Full process and resource tracing',
          'Available for any binary or script',
          'Automated with recommendations',
          'Detects idle time, silent errors, and inefficiencies',
          'Enhanced with observability and optimization',
          'End-to-end coverage: task, process, system, and cost levels'
        ]
      }
    ]
  };

  const config = {
    type: 'radar',
    data: data,
    options: {
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: 1.2,
      backgroundColor: 'transparent',
      scales: {
        r: {
          min: 0,
          max: 5,
          ticks: {
            display: false,
            stepSize: 1
          },
          grid: {
            circular: true,
            color: gridColor,
            lineWidth: 1
          },
          angleLines: {
            display: true,
            color: gridColor,
            lineWidth: 1
          },
          pointLabels: {
            font: {
              family: '"Britti Sans Trial", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
              size: 16,
              weight: '400'
            },
            color: textColor,
            padding: 35
          }
        }
      },
      plugins: {
        legend: {
          display: false
        },
        tooltip: {
          enabled: false
        }
      },
      interaction: {
        mode: 'point',
        intersect: false
      }
    }
  };

  // Point-in-polygon algorithm
  function isPointInPolygon(x, y, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const xi = polygon[i].x, yi = polygon[i].y;
      const xj = polygon[j].x, yj = polygon[j].y;
      if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
        inside = !inside;
      }
    }
    return inside;
  }

  // Get polygon points for a dataset
  function getDatasetPolygon(chartInstance, datasetIndex) {
    const meta = chartInstance.getDatasetMeta(datasetIndex);
    const points = [];
    meta.data.forEach(point => {
      points.push({ x: point.x, y: point.y });
    });
    return points;
  }

  // Check which dataset the mouse is hovering over
  function getHoveredDataset(chartInstance, mouseX, mouseY) {
    // Check Seqera-ONLY first (index 0) since it's smaller and should take priority
    const polygon0 = getDatasetPolygon(chartInstance, 0);
    if (isPointInPolygon(mouseX, mouseY, polygon0)) {
      return 0;
    }
    // Then check Seqera + Tracer (index 1)
    const polygon1 = getDatasetPolygon(chartInstance, 1);
    if (isPointInPolygon(mouseX, mouseY, polygon1)) {
      return 1;
    }
    return -1;
  }

  function highlightDataset(chartInstance, datasetIndex) {
    chartInstance.data.datasets.forEach((dataset, i) => {
      if (i === datasetIndex) {
        dataset.borderColor = i === 0 ? '#3b82f6' : '#27BF9F';
        dataset.backgroundColor = i === 0 ? 'rgba(59, 130, 246, 0.25)' : 'rgba(39, 191, 159, 0.25)';
        dataset.pointBackgroundColor = i === 0 ? '#3b82f6' : '#27BF9F';
        dataset.pointBorderColor = '#fff';
        dataset.borderWidth = 3.5;
      } else {
        dataset.borderColor = i === 0 ? 'rgba(59, 130, 246, 0.2)' : 'rgba(39, 191, 159, 0.2)';
        dataset.backgroundColor = i === 0 ? 'rgba(59, 130, 246, 0.05)' : 'rgba(39, 191, 159, 0.05)';
        dataset.pointBackgroundColor = i === 0 ? 'rgba(59, 130, 246, 0.2)' : 'rgba(39, 191, 159, 0.2)';
        dataset.pointBorderColor = 'rgba(255, 255, 255, 0.2)';
        dataset.borderWidth = 2;
      }
    });
    chartInstance.update('none');
  }

  function resetHighlight(chartInstance) {
    chartInstance.data.datasets[0].borderColor = '#3b82f6';
    chartInstance.data.datasets[0].backgroundColor = 'rgba(59, 130, 246, 0.15)';
    chartInstance.data.datasets[0].pointBackgroundColor = '#3b82f6';
    chartInstance.data.datasets[0].pointBorderColor = '#fff';
    chartInstance.data.datasets[0].borderWidth = 2.5;

    chartInstance.data.datasets[1].borderColor = '#27BF9F';
    chartInstance.data.datasets[1].backgroundColor = 'rgba(39, 191, 159, 0.15)';
    chartInstance.data.datasets[1].pointBackgroundColor = '#27BF9F';
    chartInstance.data.datasets[1].pointBorderColor = '#fff';
    chartInstance.data.datasets[1].borderWidth = 2.5;

    chartInstance.update('none');
  }

  const chart = new Chart(canvas, config);
  console.log('Radar chart initialized successfully');

  // Helper to find nearest point
  function getNearestPoint(chartInstance, mouseX, mouseY) {
    let nearestPoint = null;
    let nearestDistance = Infinity;
    const hitRadius = 15; // pixels

    chartInstance.data.datasets.forEach((dataset, datasetIndex) => {
      const meta = chartInstance.getDatasetMeta(datasetIndex);
      meta.data.forEach((point, index) => {
        const dx = point.x - mouseX;
        const dy = point.y - mouseY;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < hitRadius && distance < nearestDistance) {
          nearestDistance = distance;
          nearestPoint = { datasetIndex, index, point };
        }
      });
    });
    return nearestPoint;
  }

  // Track currently hovered point for visual effect
  let currentHoveredPoint = null;

  // Store default point radius arrays
  const defaultPointRadius = [4, 4, 4, 4, 4, 4, 4, 4, 4];
  const defaultPointBorderWidth = [2, 2, 2, 2, 2, 2, 2, 2, 2];

  // Apply hover effect to a point
  function applyPointHover(datasetIndex, pointIndex) {
    // Create arrays for the hovered state
    const hoverRadius = [...defaultPointRadius];
    const hoverBorderWidth = [...defaultPointBorderWidth];
    const hoverBgColor = chart.data.datasets[datasetIndex].data.map(() =>
      datasetIndex === 0 ? '#3b82f6' : '#27BF9F'
    );
    const hoverBorderColor = chart.data.datasets[datasetIndex].data.map(() => '#fff');

    // Set the hovered point to be larger
    hoverRadius[pointIndex] = 14;
    hoverBorderWidth[pointIndex] = 4;
    hoverBgColor[pointIndex] = '#fff';
    hoverBorderColor[pointIndex] = datasetIndex === 0 ? '#3b82f6' : '#27BF9F';

    chart.data.datasets[datasetIndex].pointRadius = hoverRadius;
    chart.data.datasets[datasetIndex].pointBorderWidth = hoverBorderWidth;
    chart.data.datasets[datasetIndex].pointBackgroundColor = hoverBgColor;
    chart.data.datasets[datasetIndex].pointBorderColor = hoverBorderColor;
  }

  // Reset point to default style
  function resetPointHover() {
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      dataset.pointRadius = [...defaultPointRadius];
      dataset.pointBorderWidth = [...defaultPointBorderWidth];
      dataset.pointBackgroundColor = datasetIndex === 0 ? '#3b82f6' : '#27BF9F';
      dataset.pointBorderColor = '#fff';
    });
  }

  // Custom mousemove handler for area detection and point tooltips
  let currentHighlightedDataset = -1;
  canvas.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    // Check for point hover first (for tooltip and visual effect)
    const nearestPoint = getNearestPoint(chart, mouseX, mouseY);
    let datasetToHighlight = -1;

    if (nearestPoint) {
      const pointKey = `${nearestPoint.datasetIndex}-${nearestPoint.index}`;
      if (currentHoveredPoint !== pointKey) {
        resetPointHover();
        applyPointHover(nearestPoint.datasetIndex, nearestPoint.index);
        currentHoveredPoint = pointKey;
        chart.update('none');
      }

      const dataset = data.datasets[nearestPoint.datasetIndex];
      const label = dataset.label;
      const value = dataset.data[nearestPoint.index];
      const description = dataset.descriptions[nearestPoint.index];
      const capability = Array.isArray(data.labels[nearestPoint.index])
        ? data.labels[nearestPoint.index].join(' ')
        : data.labels[nearestPoint.index];
      showTooltip(e, label, capability, value, description);

      // When hovering a point, highlight that point's dataset
      datasetToHighlight = nearestPoint.datasetIndex;
    } else {
      if (currentHoveredPoint !== null) {
        resetPointHover();
        currentHoveredPoint = null;
        chart.update('none');
      }
      hideTooltip();

      // Only use area detection when not hovering a point
      datasetToHighlight = getHoveredDataset(chart, mouseX, mouseY);
    }

    // Apply highlighting based on point or area detection
    if (datasetToHighlight !== -1) {
      if (datasetToHighlight !== currentHighlightedDataset) {
        currentHighlightedDataset = datasetToHighlight;
        highlightDataset(chart, datasetToHighlight);
      }
    } else {
      if (currentHighlightedDataset !== -1) {
        currentHighlightedDataset = -1;
        resetHighlight(chart);
      }
    }
  });

  // Reset highlight when mouse leaves the canvas
  canvas.addEventListener('mouseleave', () => {
    currentHighlightedDataset = -1;
    currentHoveredPoint = null;
    resetPointHover();
    resetHighlight(chart);
    hideTooltip();
  });

  // Make highlight functions available for legend (if legend is added externally)
  window.radarChartHighlight = (datasetIndex) => highlightDataset(chart, datasetIndex);
  window.radarChartReset = () => resetHighlight(chart);

  // Handle dark mode changes
  const observer = new MutationObserver(() => {
    const isDark = document.documentElement.classList.contains('dark');
    const gridColor = isDark ? '#374151' : '#e5e7eb';
    const textColor = isDark ? '#ffffff' : '#374151';
    
    chart.options.scales.r.grid.color = gridColor;
    chart.options.scales.r.angleLines.color = gridColor;
    chart.options.scales.r.pointLabels.color = textColor;
    chart.update();
  });

  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class']
  });
}

function showTooltip(event, label, capability, value, description) {
  let tooltip = document.getElementById('radar-tooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.id = 'radar-tooltip';
    tooltip.className = 'radar-tooltip';
    document.body.appendChild(tooltip);
  }

  const color = label === 'Seqera-ONLY' ? '#3b82f6' : '#27BF9F';
  const colorClass = label === 'Seqera-ONLY' ? 'seqera' : 'tracer';

  tooltip.innerHTML = `
    <div class="tooltip-title">${label}</div>
    <div class="tooltip-content">
      <strong>${capability}</strong><br/>
      Score: ${value}/5<br/>
      ${description}
    </div>
  `;

  tooltip.style.left = event.native.pageX + 10 + 'px';
  tooltip.style.top = event.native.pageY + 10 + 'px';
  tooltip.classList.add('show');
}

function hideTooltip() {
  const tooltip = document.getElementById('radar-tooltip');
  if (tooltip) {
    tooltip.classList.remove('show');
  }
}

// Initialize when DOM is ready
console.log('Radar chart script loaded');

if (document.readyState === 'loading') {
  console.log('Waiting for DOMContentLoaded...');
  document.addEventListener('DOMContentLoaded', initRadarChart);
} else {
  console.log('DOM already loaded, initializing immediately...');
  initRadarChart();
}

// Also try to initialize when the page is fully loaded
window.addEventListener('load', function() {
  console.log('Window loaded, checking if chart needs initialization...');
  if (!document.getElementById('seqera-tracer-radar-chart')?.chart) {
    initRadarChart();
  }
});

