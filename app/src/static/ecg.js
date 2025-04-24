document.addEventListener("DOMContentLoaded", function () {
  const MAX_STATS = 300;

  const stats = {
    I: [], II: [], III: [], aVR: [], aVL: [], aVF: []
  };

  // Create a Chart.js instance with real-time streaming
  function createChart(ctx, label) {
    return new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [{
          label,
          data: [],
          borderColor: 'lime',
          borderWidth: 1,
          pointRadius: 0,
          tension: 0.2
        }]
      },
      options: {
        responsive: true,
        animation: false,
        interaction: {
          mode: 'nearest',
          axis: 'xy',
          intersect: false
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: true,
            mode: 'nearest',
            intersect: false,
            callbacks: {
              label: function (context) {
                const yVal = context.parsed.y.toFixed(3);
                return `Voltage: ${yVal} mV`;
              },
              title: function (context) {
                const timestamp = context[0].parsed.x;
                const date = new Date(timestamp);
                return date.toLocaleTimeString() + '.' + date.getMilliseconds().toString().padStart(3, '0');
              }
            }
          }
        },
        scales: {
          x: {
            type: 'realtime',
            realtime: {
              duration: 10000,    // 🕒 Show 10 seconds on screen (matches 25mm/sec sweep)
              refresh: 50,        // 🔄 Redraw chart every 50ms (~20 FPS)
              delay: 100,         // ⏳ Render slightly behind real time for smoother updates
              ttl: 20000          // 🧹 Keep data for 20s in memory (for stats, if needed)
            },
            grid: {
              drawTicks: true,
              color: (ctx) => {
                const val = ctx.tick.value;
                const ms = Math.round(val % 1000);

                if (ms % 200 === 0) {
                  return 'rgba(255,255,255,0.25)';  // 🟥 Bold line every 0.2s (major ECG box)
                } else if (ms % 40 === 0) {
                  return 'rgba(255,255,255,0.07)';  // 🟧 Fine line every 0.04s (minor ECG box)
                } else {
                  return 'transparent';            // hide all others
                }
              },
              borderDash: [4, 2],  // 🟠 Dashed lines for ECG-style look
              tickLength: 4        // Short tick marks at bottom
            },
           ticks: {
             callback: (val) => '',  // Hide labels (optional)
             stepSize: 40            // Helps stabilize minor tick spacing
           },

            title: {
              display: true,
              text: 'Time (s)'
            }
          },
          y: {
            min: -0.75,
            max: 0.75,
            grid: {
              display: true,
              color: 'rgba(255, 255, 255, 0.1)'
            },
            title: {
              display: true,
              text: 'Voltage (mV)'
            }
          }
        }
      }
    });
  }


  const charts = {
    I: createChart(document.getElementById("leadI"), "Lead I"),
    II: createChart(document.getElementById("leadII"), "Lead II"),
    III: createChart(document.getElementById("leadIII"), "Lead III"),
    aVR: createChart(document.getElementById("leadaVR"), "aVR"),
    aVL: createChart(document.getElementById("leadaVL"), "aVL"),
    aVF: createChart(document.getElementById("leadaVF"), "aVF")
  };

  let lastPeakTime = 0;
  let bpmHistory = [];


  const source = new EventSource("/api/stream");

  source.onmessage = function(event) {
    const payload = JSON.parse(event.data);

    const leads = payload.leads;
    const now = Date.now();

    if ("bpm" in payload) {
      const bpmDisplay = document.getElementById("bpm-value");
      if (bpmDisplay) {
        bpmDisplay.innerText = `${payload.bpm.toFixed(1)}`;
      }
    }

    ["I", "II", "III", "aVR", "aVL", "aVF"].forEach((lead) => {
      const chart = charts[lead];
      const val = leads[lead];

      if (typeof val !== 'number') {
        console.warn(`Missing or invalid value for lead ${lead}`);
        return;
      }

      chart.data.datasets[0].data.push({ x: now, y: val });

      stats[lead].push(val);
      if (stats[lead].length > MAX_STATS) stats[lead].shift();

      const min = Math.min(...stats[lead]);
      const max = Math.max(...stats[lead]);
      const avg = stats[lead].reduce((a, b) => a + b, 0) / stats[lead].length;

      document.getElementById(`val-${lead}`).innerText = val.toFixed(3);
      document.getElementById(`min-${lead}`).innerText = min.toFixed(3);
      document.getElementById(`max-${lead}`).innerText = max.toFixed(3);
      document.getElementById(`avg-${lead}`).innerText = avg.toFixed(3);
    });
  };

});
