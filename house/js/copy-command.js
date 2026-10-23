/**
 * Wires every install box under `root` (see css/install.css).
 *
 * A tab with `data-command` puts its command into the install box named by its
 * `aria-controls`. A button with `data-copy` copies that text, says so for a
 * moment, and then shows its original label again. Calling this again on the
 * same page binds nothing twice.
 *
 * @param {ParentNode} [root=document] Where to look for tabs and copy buttons.
 */
export function initCopyCommands(root = document) {
  for (const tab of root.querySelectorAll('.hs-install-tab[data-command]')) {
    if (tab.dataset.hsBound) continue;
    tab.dataset.hsBound = 'true';
    tab.addEventListener('click', () => selectCommand(tab));
  }
  for (const button of root.querySelectorAll('[data-copy]')) {
    if (button.dataset.hsBound) continue;
    button.dataset.hsBound = 'true';
    button.addEventListener('click', () => copy(button));
  }
}

function selectCommand(tab) {
  const tabs = tab.closest('[role="tablist"]')?.querySelectorAll('.hs-install-tab') ?? [tab];
  for (const other of tabs) {
    const isCurrent = other === tab;
    other.classList.toggle('is-active', isCurrent);
    other.setAttribute('aria-selected', isCurrent ? 'true' : 'false');
  }

  const box = document.getElementById(tab.getAttribute('aria-controls') ?? '');
  if (!box) return;
  const command = tab.dataset.command ?? '';
  const code = box.querySelector('code');
  if (code) {
    code.textContent = command;
    code.scrollLeft = 0;
  }
  const button = box.querySelector('[data-copy]');
  if (button) button.dataset.copy = command;
}

async function copy(button) {
  const idle = button.dataset.hsIdle ?? button.innerHTML;
  button.dataset.hsIdle = idle;
  try {
    await navigator.clipboard.writeText(button.dataset.copy ?? '');
    button.textContent = 'Copied!';
  } catch {
    button.textContent = 'Failed';
  }
  window.clearTimeout(Number(button.dataset.hsTimer));
  button.dataset.hsTimer = String(
    window.setTimeout(() => {
      button.innerHTML = idle;
    }, 1800),
  );
}
