<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let tab = 'records'
  let rows = []
  let herb = '白芍'
  let tempC = 110
  let minutes = 10
  let barcode = ''
  let error = ''

  let barcodes = []
  let ledger = []
  let newHerb = '白芍'
  let winStart = ''
  let winEnd = ''
  let barcodeMsg = ''
  let barcodeError = ''

  $: activeBarcodes = barcodes.filter((b) => b.status === 'active')
  $: voidedLedger = ledger.filter((e) => e.event === 'voided')

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    token = data.access_token
    role = data.role
    localStorage.setItem('herb_token', token)
    localStorage.setItem('herb_role', role)
    resetWindowDefaults()
    await load()
  }

  async function load() {
    rows = await api('/api/batches')
    await Promise.all([loadBarcodes(), loadLedger()])
  }

  async function loadBarcodes() {
    barcodes = await api('/api/barcodes')
  }

  async function loadLedger() {
    ledger = await api('/api/barcodes/ledger')
  }

  async function save() {
    error = ''
    if (!barcode) {
      error = '请先选择留样条码'
      return
    }
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          barcode,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      barcode = ''
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function generateBarcode() {
    barcodeMsg = ''
    barcodeError = ''
    if (!winStart || !winEnd) {
      barcodeError = '请填写取样窗口起止时刻'
      return
    }
    try {
      const b = await api('/api/barcodes', {
        method: 'POST',
        body: JSON.stringify({
          herb: newHerb,
          window_start: new Date(winStart).toISOString(),
          window_end: new Date(winEnd).toISOString(),
        }),
      })
      barcodeMsg = `已生成留样条码 ${b.code}`
      await Promise.all([loadBarcodes(), loadLedger()])
    } catch (err) {
      barcodeError = err.message
    }
  }

  async function voidBarcode(id) {
    barcodeMsg = ''
    barcodeError = ''
    try {
      await api(`/api/barcodes/${id}/void`, { method: 'POST' })
      await Promise.all([loadBarcodes(), loadLedger()])
    } catch (err) {
      barcodeError = err.message
    }
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
  }

  function toLocalInput(d) {
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  }

  function resetWindowDefaults() {
    const now = new Date()
    winStart = toLocalInput(now)
    winEnd = toLocalInput(new Date(now.getTime() + 2 * 3600 * 1000))
  }

  function fmt(iso) {
    if (!iso) return ''
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  }

  function windowState(b) {
    const now = Date.now()
    if (now < new Date(b.window_start).getTime()) return '未开始'
    if (now > new Date(b.window_end).getTime()) return '已过期'
    return '窗口内'
  }

  resetWindowDefaults()
  if (token) load()
</script>

<main>
  <h1>饮片炮制记录台</h1>
  {#if !token}
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。开炒前须先预约留样条码。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <p>
      <button on:click={leave}>退出</button>
    </p>
    <nav>
      <button class:active={tab === 'records'} on:click={() => (tab = 'records')}>炮制记录</button>
      <button class:active={tab === 'barcodes'} on:click={() => (tab = 'barcodes')}>留样条码</button>
    </nav>

    {#if tab === 'records'}
      {#if role === 'writer'}
        <section>
          <h2>写入清炒记录</h2>
          <input bind:value={herb} placeholder="饮片" />
          <input type="number" bind:value={tempC} />
          <input type="number" bind:value={minutes} />
          <select bind:value={barcode}>
            <option value="">选择留样条码</option>
            {#each activeBarcodes as b}
              <option value={b.code}>{b.code} · {b.herb} · {fmt(b.window_start)} ~ {fmt(b.window_end)}</option>
            {/each}
          </select>
          <button on:click={save}>写入清炒记录</button>
          {#if activeBarcodes.length === 0}<p>暂无有效留样条码，请先到「留样条码」页预约生成。</p>{/if}
          {#if error}<p class="err">{error}</p>{/if}
        </section>
      {/if}
      <ul>
        {#each rows as row}
          <li>{row.herb} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}</li>
        {/each}
      </ul>
    {:else}
      {#if role === 'writer'}
        <section>
          <h2>预约生成</h2>
          <input bind:value={newHerb} placeholder="饮片" />
          <label>窗口起 <input type="datetime-local" bind:value={winStart} /></label>
          <label>窗口止 <input type="datetime-local" bind:value={winEnd} /></label>
          <button on:click={generateBarcode}>生成留样条码</button>
          {#if barcodeMsg}<p class="ok">{barcodeMsg}</p>{/if}
          {#if barcodeError}<p class="err">{barcodeError}</p>{/if}
        </section>
      {/if}
      <section>
        <h2>有效条码列表</h2>
        {#if activeBarcodes.length === 0}
          <p>暂无有效留样条码。</p>
        {:else}
          <table>
            <thead>
              <tr><th>条码串</th><th>饮片</th><th>窗口起</th><th>窗口止</th><th>窗口状态</th>{#if role === 'writer'}<th>操作</th>{/if}</tr>
            </thead>
            <tbody>
              {#each activeBarcodes as b}
                <tr>
                  <td>{b.code}</td>
                  <td>{b.herb}</td>
                  <td>{fmt(b.window_start)}</td>
                  <td>{fmt(b.window_end)}</td>
                  <td>{windowState(b)}</td>
                  {#if role === 'writer'}<td><button on:click={() => voidBarcode(b.id)}>作废</button></td>{/if}
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
      <section>
        <h2>作废流水</h2>
        {#if voidedLedger.length === 0}
          <p>暂无作废记录。</p>
        {:else}
          <table>
            <thead>
              <tr><th>条码串</th><th>饮片</th><th>操作人</th><th>作废时刻</th><th>备注</th></tr>
            </thead>
            <tbody>
              {#each voidedLedger as e}
                <tr>
                  <td>{e.code}</td>
                  <td>{e.herb}</td>
                  <td>{e.actor}</td>
                  <td>{fmt(e.created_at)}</td>
                  <td>{e.note}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 860px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  h2 { color: #7c2d12; font-size: 18px; margin: 0 0 10px; }
  input, select { margin-right: 8px; padding: 6px; }
  nav { margin: 12px 0 20px; border-bottom: 2px solid #e7d8c9; }
  nav button { background: none; border: none; padding: 8px 16px; font-size: 16px; cursor: pointer; color: #7c2d12; }
  nav button.active { border-bottom: 3px solid #7c2d12; font-weight: bold; }
  section { background: #faf5ef; border: 1px solid #e7d8c9; border-radius: 8px; padding: 14px 16px; margin-bottom: 18px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #e7d8c9; padding: 6px 10px; text-align: left; font-size: 14px; }
  th { background: #f0e4d7; }
  .err { color: #b91c1c; }
  .ok { color: #15803d; }
</style>
