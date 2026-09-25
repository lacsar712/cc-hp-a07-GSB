<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let view = 'records'

  let rows = []
  let herb = '白芍'
  let tempC = 88
  let minutes = 9
  let barcodeCode = ''
  let error = ''

  let activeCodes = []
  let voids = []
  let rHerb = '白芍'
  let rStart = localInputValue(new Date(Date.now() - 3600_000))
  let rEnd = localInputValue(new Date(Date.now() + 3600_000))
  let reserveMsg = ''
  let reserveErr = ''
  let vCode = ''
  let vReason = ''
  let voidMsg = ''
  let voidErr = ''

  function localInputValue(d) {
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  }

  function fmt(ts) {
    return ts ? new Date(ts).toLocaleString() : '—'
  }

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
    await switchView('records')
  }

  async function loadBatches() {
    rows = await api('/api/batches')
  }

  async function loadBarcodes() {
    activeCodes = await api('/api/barcodes?scope=active')
    voids = await api('/api/barcode-voids')
  }

  async function switchView(v) {
    view = v
    error = reserveErr = voidErr = ''
    if (v === 'records') await loadBatches()
    else await loadBarcodes()
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          barcode: barcodeCode.trim(),
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      barcodeCode = ''
      await loadBatches()
    } catch (err) {
      error = err.message
    }
  }

  async function reserve() {
    reserveMsg = ''
    reserveErr = ''
    try {
      const created = await api('/api/barcodes', {
        method: 'POST',
        body: JSON.stringify({
          herb: rHerb,
          window_start: new Date(rStart).toISOString(),
          window_end: new Date(rEnd).toISOString(),
        }),
      })
      reserveMsg = `已生成条码 ${created.code}`
      await loadBarcodes()
    } catch (err) {
      reserveErr = err.message
    }
  }

  async function voidCode() {
    voidMsg = ''
    voidErr = ''
    try {
      await api(`/api/barcodes/${encodeURIComponent(vCode.trim())}/void`, {
        method: 'POST',
        body: JSON.stringify({ reason: vReason }),
      })
      voidMsg = `条码 ${vCode} 已作废并进流水`
      vCode = ''
      vReason = ''
      await loadBarcodes()
    } catch (err) {
      voidErr = err.message
    }
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
  }

  if (token) switchView('records')
</script>

<main>
  <h1>饮片炮制记录台</h1>
  {#if !token}
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。写入须挂开炒前预约的有效留样条码。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <nav>
      <button class:active={view === 'records'} on:click={() => switchView('records')}>炮制记录</button>
      <button class:active={view === 'barcodes'} on:click={() => switchView('barcodes')}>留样条码</button>
      <span class="spacer"></span>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view === 'records'}
      <section>
        {#if role === 'writer'}
          <h2>写入清炒记录</h2>
          <div class="formrow">
            <input bind:value={herb} placeholder="饮片" />
            <input type="number" bind:value={tempC} placeholder="温度℃" />
            <input type="number" bind:value={minutes} placeholder="分钟" />
            <input bind:value={barcodeCode} placeholder="留样条码" />
            <button on:click={save}>挂码写入</button>
          </div>
          {#if error}<p class="err">{error}</p>{/if}
        {/if}
        <h2>记录列表</h2>
        <ul>
          {#each rows as row}
            <li>{row.herb} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}℃ · 条码 {row.barcode || '—'}</li>
          {/each}
        </ul>
      </section>
    {:else}
      <section>
        <h2>预约生成</h2>
        {#if role === 'writer'}
          <div class="formrow">
            <input bind:value={rHerb} placeholder="饮片（如：白芍）" />
            <label>窗口起 <input type="datetime-local" bind:value={rStart} /></label>
            <label>窗口止 <input type="datetime-local" bind:value={rEnd} /></label>
            <button on:click={reserve}>预约条码</button>
          </div>
          {#if reserveMsg}<p class="ok">{reserveMsg}</p>{/if}
          {#if reserveErr}<p class="err">{reserveErr}</p>{/if}
        {:else}
          <p class="hint">质检员仅可查阅，不能预约生成条码。</p>
        {/if}

        <h2>有效条码列表</h2>
        <ul>
          {#each activeCodes as c}
            <li>
              <strong>{c.code}</strong> · {c.herb} · 窗口 {fmt(c.window_start)} 至 {fmt(c.window_end)}
            </li>
          {:else}
            <li class="hint">暂无有效条码。</li>
          {/each}
        </ul>

        <h2>作废流水</h2>
        {#if role === 'writer'}
          <div class="formrow">
            <input bind:value={vCode} placeholder="要作废的条码" />
            <input bind:value={vReason} placeholder="作废原因" />
            <button on:click={voidCode}>作废并进流水</button>
          </div>
          {#if voidMsg}<p class="ok">{voidMsg}</p>{/if}
          {#if voidErr}<p class="err">{voidErr}</p>{/if}
        {:else}
          <p class="hint">质检员仅可查阅，不能作废条码。</p>
        {/if}
        <table>
          <thead>
            <tr><th>条码</th><th>饮片</th><th>原因</th><th>作废人</th><th>作废时刻</th></tr>
          </thead>
          <tbody>
            {#each voids as v}
              <tr>
                <td>{v.code}</td><td>{v.herb}</td><td>{v.reason}</td>
                <td>{v.voided_by}</td><td>{fmt(v.voided_at)}</td>
              </tr>
            {:else}
              <tr><td colspan="5" class="hint">暂无作废流水。</td></tr>
            {/each}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 860px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  h2 { margin-top: 28px; font-size: 18px; }
  nav { display: flex; align-items: center; gap: 8px; margin: 16px 0; }
  nav button { padding: 6px 14px; }
  nav button.active { font-weight: bold; background: #7c2d12; color: #fff; }
  .spacer { flex: 1; }
  .formrow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  input { padding: 6px; }
  ul { padding-left: 18px; }
  li { margin: 4px 0; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid #d8c7b4; padding: 6px 8px; text-align: left; font-size: 14px; }
  .err { color: #b91c1c; }
  .ok { color: #15803d; }
  .hint { color: #8a7a6a; }
</style>
