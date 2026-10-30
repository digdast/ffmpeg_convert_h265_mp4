// Space Savings Calculator Logic
function updateCalculator() {
  const sizeInput = document.getElementById('libSize');
  const sizeVal = document.getElementById('sizeVal');
  const codecSelect = document.getElementById('sourceCodec');
  const savedNumber = document.getElementById('savedNumber');
  const resultDetail = document.getElementById('resultDetail');

  if (!sizeInput || !codecSelect || !savedNumber) return;

  const sizeGB = parseFloat(sizeInput.value);
  sizeVal.textContent = sizeGB >= 1000 ? (sizeGB / 1000).toFixed(1) + ' TB' : sizeGB + ' GB';

  const reductionFactors = {
    'h264': 0.58,   // ~58% reduction from typical x264
    'vc1': 0.65,    // ~65% reduction from old VC1 remuxes
    'mpeg2': 0.72,  // ~72% reduction from ancient MPEG2 DVDs
    'mixed': 0.60   // ~60% average mix
  };

  const factor = reductionFactors[codecSelect.value] || 0.60;
  const savedGB = sizeGB * factor;
  const newSizeGB = sizeGB - savedGB;

  if (savedGB >= 1000) {
    savedNumber.textContent = (savedGB / 1000).toFixed(2) + ' TB';
  } else {
    savedNumber.textContent = Math.round(savedGB) + ' GB';
  }

  const newDisplay = newSizeGB >= 1000 ? (newSizeGB / 1000).toFixed(2) + ' TB' : Math.round(newSizeGB) + ' GB';
  resultDetail.textContent = `Library reduced from ${sizeVal.textContent} down to ~${newDisplay} (${Math.round(factor * 100)}% net savings)`;
}

// Preset Size Selector Helper
function setLibSize(gb) {
  const sizeInput = document.getElementById('libSize');
  if (sizeInput) {
    sizeInput.value = gb;
    updateCalculator();
  }
}

// Copy Code Button Helper
function copyCode(btn, codeId) {
  const el = document.getElementById(codeId);
  if (!el) return;
  navigator.clipboard.writeText(el.innerText).then(() => {
    const originalText = btn.innerHTML;
    btn.innerHTML = '✔ Copied!';
    setTimeout(() => {
      btn.innerHTML = originalText;
    }, 2000);
  });
}

// Copy Direct Text Helper
function copyText(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    const originalText = btn.innerHTML;
    btn.innerHTML = '✔ Copied!';
    setTimeout(() => {
      btn.innerHTML = originalText;
    }, 2000);
  });
}

// OS Installer Tab Switching
function switchTab(tabId) {
  // Update buttons
  document.querySelectorAll('.os-tab-btn').forEach(btn => {
    btn.classList.remove('active');
    if (btn.getAttribute('data-tab') === tabId) {
      btn.classList.add('active');
    }
  });

  // Update panes
  document.querySelectorAll('.tab-pane').forEach(pane => {
    pane.classList.remove('active');
    if (pane.id === 'tab-' + tabId) {
      pane.classList.add('active');
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const sizeInput = document.getElementById('libSize');
  const codecSelect = document.getElementById('sourceCodec');
  if (sizeInput) sizeInput.addEventListener('input', updateCalculator);
  if (codecSelect) codecSelect.addEventListener('change', updateCalculator);
  updateCalculator();

  // Tab button click listeners
  document.querySelectorAll('.os-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.getAttribute('data-tab');
      if (tab) switchTab(tab);
    });
  });
});


