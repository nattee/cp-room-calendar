'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const rooms = JSON.parse($('rooms-data').textContent);
  const zone = 'Asia/Bangkok';
  const dayString = d => new Intl.DateTimeFormat('en-CA', {timeZone: zone, year:'numeric', month:'2-digit', day:'2-digit'}).format(d);
  const timeString = d => new Intl.DateTimeFormat('en-GB', {timeZone: zone, hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).format(d);
  let roomIndex = 0, payload = null, controller = null, timer = null, loading = false, failed = false;
  let selectedDate = dayString(new Date()), followsToday = true, lastFetch = 0, viewMode = 'day';
  let calendarSignature = '';
  let allLayout = window.matchMedia('(max-width:700px)').matches ? 'list' : 'grid';
  try { const saved = localStorage.getItem('cp-view'); if (saved === 'month' || saved === 'all') viewMode = saved; } catch (_) {}
  try { const saved = localStorage.getItem('cp-all-layout'); if (saved === 'grid' || saved === 'list') allLayout = saved; } catch (_) {}
  const labelForDate = value => new Intl.DateTimeFormat('th-TH', {weekday:'short', day:'numeric', month:'short', year:'numeric', timeZone:zone}).format(new Date(`${value}T12:00:00+07:00`));
  const shiftedDate = (value, days) => { const d = new Date(`${value}T12:00:00+07:00`); d.setUTCDate(d.getUTCDate()+days); return dayString(d); };
  const initialNames = new Map(rooms.map(r => [r.key, r.name]));
  const roomLabel = room => room.name || 'กำลังโหลดชื่อห้อง…';

  try { roomIndex = Math.max(0, rooms.findIndex(r => r.key === localStorage.getItem('cp-room'))); } catch (_) {}
  function element(tag, text, className) { const e = document.createElement(tag); if(text !== undefined) e.textContent = text; if(className) e.className = className; return e; }
  function notify(text) { $('notice').textContent = text || ''; $('notice').hidden = !text; }
  function unknown(text) { $('availability').className = 'availability unknown'; $('availability-text').textContent = text; }
  function skeleton() { $('booking').hidden = true; $('all-body').replaceChildren(); $('event-list').replaceChildren(...[1,2,3].map(() => element('div', undefined, 'skeleton'))); renderMonth([], false); }
  function updateRoomName(key, name) {
    const room = rooms.find(r => r.key === key);
    if (!room || !name) return;
    room.name = initialNames.get(key) || name;
    const option = [...$('room-select').options].find(o => o.value === key);
    if (option) option.textContent = room.name;
    if (rooms[roomIndex].key === key) $('room-name').textContent = room.name;
  }
  async function loadRoomNames() {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 45000);
    try {
      const response = await fetch('/api/rooms', {credentials:'same-origin',cache:'no-store',signal:abort.signal});
      if (!response.ok) throw new Error('names unavailable');
      const data = await response.json();
      for (const room of data.rooms) {
        // Do not replace a name already obtained from a successful booking request with an error label.
        if (room.resolved !== false || !rooms.find(r => r.key === room.key)?.name) updateRoomName(room.key, room.name);
      }
      const missing = data.rooms.some(r => r.resolved === false);
      $('room-name-note').hidden = !missing;
      $('room-name-note').textContent = missing ? 'บางชื่อห้องโหลดไม่ได้ โปรดตรวจสอบสิทธิ์ปฏิทิน / Some calendar names are unavailable.' : '';
    } catch (_) {
      $('room-name-note').hidden = false;
      $('room-name-note').textContent = 'โหลดชื่อห้องไม่สำเร็จ ลองรีเฟรชหน้า / Could not load room names. Refresh to retry.';
      rooms.forEach(r => { if (!r.name) updateRoomName(r.key, `ชื่อห้องไม่พร้อมใช้งาน (${r.key})`); });
    } finally { clearTimeout(timeout); }
  }

  function eventTime(value) { return new Date(value.dateTime || `${value.date}T00:00:00+07:00`); }
  function eventsOnDay(events, value) {
    const start = new Date(`${value}T00:00:00+07:00`), end = new Date(`${shiftedDate(value,1)}T00:00:00+07:00`);
    return events.filter(e => e.startAt < end && e.endAt > start).sort((a,b) => a.startAt-b.startAt);
  }
  function bookingStart(events) {
    // Suggest the first free hour: from the next whole hour when looking at today, otherwise from 09:00.
    const now = new Date();
    const first = dayString(now) === selectedDate ? Number(timeString(now).slice(0,2))+1 : 9;
    const busy = events.filter(e => e.busy);
    const label = hour => `${String(hour).padStart(2,'0')}:00`;
    for (let hour = first; hour <= 21; hour++) {
      const start = new Date(`${selectedDate}T${label(hour)}:00+07:00`), end = new Date(start.getTime()+3600000);
      if (!busy.some(e => e.startAt < end && e.endAt > start)) return label(hour);
    }
    return label(Math.min(first,23));
  }
  function gridBounds() {
    const first = selectedDate.slice(0,7) + '-01';
    const d = new Date(`${first}T12:00:00+07:00`);
    const start = shiftedDate(first, -(d.getUTCDay()+6)%7);
    d.setUTCMonth(d.getUTCMonth()+1);
    const following = dayString(d);
    return [start, shiftedDate(following, (7-(d.getUTCDay()+6)%7)%7)];
  }
  function eventPeriod(item) {
    if(item.start.date) return 'allday';
    const hour=Number(timeString(item.startAt).slice(0,2));
    return hour<12 ? 'am' : hour<16 ? 'pm' : 'evening';
  }
  let fitFrame=0;
  function queueCalendarFit() {
    cancelAnimationFrame(fitFrame);
    fitFrame=requestAnimationFrame(()=>{
      if(viewMode!=='month') return;
      for(const cell of $('month-days').children) {
        const box=cell.querySelector('.calendar-bookings');
        const chips=[...box.querySelectorAll('.calendar-booking')];
        chips.forEach(chip=>chip.hidden=false);
        let visible=0;
        for(const chip of chips) {
          if(visible===0 || chip.offsetTop+chip.offsetHeight<=box.clientHeight) visible++;
          else break;
        }
        chips.forEach((chip,i)=>chip.hidden=i>=visible);
        const more=cell.querySelector('.calendar-more');
        more.textContent=chips.length>visible ? `+${chips.length-visible}` : '';
        more.hidden=chips.length<=visible;
        more.title=[...new Set(chips.slice(visible).map(c=>({am:'ก่อน 12:00',pm:'12:00–16:00',evening:'ตั้งแต่ 16:00',allday:'ทั้งวัน'}[c.dataset.period])))].join(', ');
      }
    });
  }
  function renderMonth(events, known) {
    if (viewMode !== 'month') return;
    const signature = JSON.stringify([selectedDate, dayString(new Date()), known, events]);
    if (signature === calendarSignature) return;
    calendarSignature = signature;
    const grid = $('month-days');
    const focusedDate = grid.contains(document.activeElement) ? document.activeElement.dataset.date : null;
    grid.replaceChildren(); grid.setAttribute('aria-busy', String(!known && loading));
    const [start,end] = gridBounds();
    for (let day = start; day < end; day = shiftedDate(day,1)) {
      const items = eventsOnDay(events,day);
      const button = element('button',undefined,'calendar-day');
      button.type = 'button'; button.dataset.date = day;
      button.classList.toggle('outside-month', day.slice(0,7)!==selectedDate.slice(0,7));
      button.classList.toggle('is-today', day===dayString(new Date()));
      button.classList.toggle('is-selected', day===selectedDate);
      button.classList.toggle('unconfirmed', !known);
      button.setAttribute('aria-pressed', String(day===selectedDate));
      if (day===dayString(new Date())) button.setAttribute('aria-current','date');
      button.setAttribute('aria-label', `${labelForDate(day)} · ${known ? `${items.length} รายการ / bookings` : 'ข้อมูลยังไม่ยืนยัน / Unconfirmed'}`);
      const dayHeader = element('span',undefined,'calendar-day-header');
      dayHeader.append(element('span',String(Number(day.slice(-2))),'calendar-number'));
      button.append(dayHeader);
      const more=element('span','','calendar-more');
      dayHeader.append(more);
      const bookings=element('span',undefined,'calendar-bookings');
      button.append(bookings);
      if (items.length) {
        for (const item of items) {
          const when=item.start.date ? 'ทั้งวัน' : dayString(item.startAt)<day ? 'ต่อเนื่อง' : timeString(item.startAt);
          const preview=element('span',undefined,`calendar-booking period-${eventPeriod(item)}`);
          preview.dataset.period=eventPeriod(item);
          preview.append(element('strong',when,'calendar-time'),document.createTextNode(' '),element('span',item.title,'calendar-preview'));
          bookings.append(preview);
        }
        button.setAttribute('aria-label',`${labelForDate(day)}, ${items.length} รายการ, ${items[0].title}${known ? '' : ', ข้อมูลอาจไม่เป็นปัจจุบัน'}`);
      } else bookings.append(element('span',known ? '—' : '…','calendar-empty'));
      button.disabled = day<'2000-01-01' || day>'2100-12-31';
      button.addEventListener('click', () => changeDate(day));
      grid.append(button);
    }
    if (focusedDate) [...grid.children].find(e => e.dataset.date===focusedDate)?.focus({preventScroll:true});
    $('selected-day-heading').textContent = labelForDate(selectedDate);
    queueCalendarFit();
  }
  function render() {
    if (!payload) return;
    if (payload.rooms) { renderAllRooms(); return; }
    const now = new Date();
    const allEvents = payload.events.map(e => ({...e, startAt:eventTime(e.start), endAt:eventTime(e.end)}));
    const events = eventsOnDay(allEvents,selectedDate);
    renderMonth(allEvents, !failed && navigator.onLine && Date.now()-lastFetch<=150000);
    const busy = events.filter(e => e.busy && e.startAt <= now && e.endAt > now);
    if (failed || !navigator.onLine || Date.now() - lastFetch > 150000) {
      unknown('ข้อมูลอาจไม่เป็นปัจจุบัน / Availability unknown');
    } else if (selectedDate !== dayString(now)) {
      unknown('กำลังดูตารางวันที่เลือก / Selected day');
    } else if (busy.length) {
      let until = Math.max(...busy.map(e => e.endAt.getTime()));
      // Merge overlapping and back-to-back bookings for an accurate busy-until time.
      for (const e of [...events].sort((a,b) => a.startAt-b.startAt)) {
        if (e.busy && e.startAt.getTime() <= until && e.endAt.getTime() > until) until = e.endAt.getTime();
      }
      $('availability').className = 'availability busy';
      $('availability-text').textContent = dayString(new Date(until)) !== selectedDate ? 'ไม่ว่างถึงสิ้นวัน / Busy through today' : `ไม่ว่างถึง ${timeString(new Date(until))} / Busy until ${timeString(new Date(until))}`;
    } else {
      const next = events.filter(e => e.busy && e.startAt > now).sort((a,b) => a.startAt-b.startAt)[0];
      $('availability').className = 'availability';
      $('availability-text').textContent = next ? `ว่างถึง ${timeString(next.startAt)} / Available until ${timeString(next.startAt)}` : 'ว่างตอนนี้ / Available now';
    }
    $('booking').hidden = !payload.can_book;
    if (payload.can_book) $('book').href = `/book?${new URLSearchParams({room:rooms[roomIndex].key, date:selectedDate, start:bookingStart(events)})}`;
    const list = $('event-list'); list.replaceChildren();
    if (!events.length) {
      const empty = element('div', undefined, 'empty');
      empty.append(element('div', '▦', 'empty-symbol'), element('h3','ไม่มีรายการจองในวันนี้'), element('p','No bookings for this day.'));
      list.append(empty);
    }
    for (const e of events) {
      const current = e.startAt <= now && e.endAt > now;
      const item = element('article', undefined, `event period-${eventPeriod(e)}${current ? ' current' : ''}${e.endAt <= now ? ' past' : ''}${!e.busy ? ' free' : ''}`);
      const times = element('div', undefined, 'event-time');
      if (e.start.date) times.append(element('span','ทั้งวัน'), element('small','All day'));
      else {
        const start = dayString(e.startAt) < selectedDate ? '00:00' : timeString(e.startAt);
        const end = dayString(e.endAt) > selectedDate ? '24:00' : timeString(e.endAt);
        times.append(element('span',start),element('small',`— ${end}`));
      }
      const detail = element('div', undefined, 'event-detail');
      detail.append(element('h3',e.title));
      detail.append(element('small',!e.busy ? 'ไม่บล็อกเวลาห้อง / Does not block room' : current ? 'กำลังใช้งาน / IN PROGRESS' : e.endAt <= now ? 'สิ้นสุดแล้ว / FINISHED' : 'จองแล้ว / BOOKED','event-label'));
      item.append(times, detail); list.append(item);
    }
    $('event-count').textContent = `${events.length} รายการ`;
  }
  const GRID_HOUR = 46;
  const hourOf = d => Number(timeString(d).slice(0,2)) + Number(timeString(d).slice(3,5))/60;
  function allEntries() {
    return payload.rooms.map(room => ({
      key: room.key, name: room.name || room.key, error: room.error, message: room.message,
      items: eventsOnDay(room.events.map(e => ({...e, startAt:eventTime(e.start), endAt:eventTime(e.end)})), selectedDate),
    }));
  }
  function timedItems(room) { return room.items.filter(i => !i.start.date); }
  function spanOf(item) {
    // Hours into the selected day, clipped to it, so overnight bookings stay inside the grid.
    const from = dayString(item.startAt) < selectedDate ? 0 : hourOf(item.startAt);
    const to = dayString(item.endAt) > selectedDate ? 24 : hourOf(item.endAt);
    return [from, Math.max(to, from + 0.25)];
  }
  function startLabel(item) { return dayString(item.startAt) < selectedDate ? '00:00' : timeString(item.startAt); }
  function endLabel(item) { return dayString(item.endAt) > selectedDate ? '24:00' : timeString(item.endAt); }
  function jumpToRoom(key) {
    const index = rooms.findIndex(r => r.key === key);
    if (index < 0) return;
    roomIndex = index;
    try {localStorage.setItem('cp-room', rooms[roomIndex].key);} catch (_) {}
    switchView('day');
  }
  function roomButton(room, className) {
    const button = element('button', room.name, className);
    button.type = 'button';
    button.title = room.error ? `${room.name} · ${room.message || ''}` : `${room.name} · ดูรายวัน / Open day view`;
    button.addEventListener('click', () => jumpToRoom(room.key));
    return button;
  }
  function buildGrid(entries) {
    let first = 8, last = 20;
    for (const room of entries) for (const item of timedItems(room)) {
      const [from, to] = spanOf(item);
      first = Math.min(first, Math.floor(from)); last = Math.max(last, Math.ceil(to));
    }
    const height = (last-first)*GRID_HOUR + 12;
    const inner = element('div', undefined, 'grid-inner');
    inner.append(element('div', undefined, 'grid-corner'));
    const heads = element('div', undefined, 'grid-heads');
    for (const room of entries) heads.append(roomButton(room, `grid-head${room.error ? ' unknown' : ''}`));
    inner.append(heads);
    if (entries.some(r => r.items.some(i => i.start.date))) {
      inner.append(element('div', 'ทั้งวัน', 'grid-allday-label'));
      const strip = element('div', undefined, 'grid-allday');
      for (const room of entries) {
        const cell = element('div', undefined, 'grid-allday-cell');
        for (const item of room.items.filter(i => i.start.date)) {
          cell.append(element('span', item.title, `grid-event period-allday${item.busy ? '' : ' free'}`));
        }
        strip.append(cell);
      }
      inner.append(strip);
    }
    const axis = element('div', undefined, 'grid-axis');
    axis.style.height = `${height}px`;
    for (let hour = first; hour <= last; hour++) {
      const label = element('span', `${String(hour).padStart(2,'0')}:00`, 'grid-hour');
      label.style.top = `${(hour-first)*GRID_HOUR}px`;
      axis.append(label);
    }
    inner.append(axis);
    const cols = element('div', undefined, 'grid-cols');
    cols.style.height = `${height}px`;
    for (const room of entries) {
      const col = element('div', undefined, `grid-col${room.error ? ' unknown' : ''}`);
      for (let hour = first; hour <= last; hour++) {
        const line = element('i'); line.style.top = `${(hour-first)*GRID_HOUR}px`; col.append(line);
      }
      if (room.error) col.append(element('span', 'ไม่ทราบสถานะ / unknown', 'grid-unavailable'));
      for (const item of timedItems(room)) {
        const [from, to] = spanOf(item);
        const block = element('div', undefined, `grid-event period-${eventPeriod(item)}${item.busy ? '' : ' free'}`);
        block.style.top = `${(from-first)*GRID_HOUR}px`;
        block.style.height = `${(to-from)*GRID_HOUR-3}px`;
        block.title = `${startLabel(item)}—${endLabel(item)} · ${room.name} · ${item.title}`;
        block.append(element('b', startLabel(item)), document.createTextNode(item.title));
        col.append(block);
      }
      cols.append(col);
    }
    const now = new Date();
    if (selectedDate === dayString(now) && hourOf(now) >= first && hourOf(now) <= last) {
      const line = element('div', undefined, 'grid-now');
      line.style.top = `${(hourOf(now)-first)*GRID_HOUR}px`;
      cols.append(line);
    }
    inner.append(cols);
    const scroll = element('div', undefined, 'grid-scroll');
    scroll.append(inner);
    return scroll;
  }
  function buildList(entries) {
    const rows = [];
    for (const room of entries) for (const item of room.items) rows.push({room, item});
    rows.sort((a,b) => (a.item.start.date ? -1 : 0) - (b.item.start.date ? -1 : 0) || a.item.startAt - b.item.startAt || a.room.name.localeCompare(b.room.name));
    const wrap = element('div', undefined, 'all-list-wrap');
    const list = element('div', undefined, 'all-list');
    for (const {room, item} of rows) {
      const row = element('article', undefined, `all-row period-${eventPeriod(item)}${item.busy ? '' : ' free'}`);
      const time = element('div', undefined, 'all-time');
      if (item.start.date) time.append(element('span','ทั้งวัน'), element('small','All day'));
      else time.append(element('span', startLabel(item)), element('small', `— ${endLabel(item)}`));
      row.append(time, roomButton(room, 'all-room'), element('div', item.title, 'all-title'));
      list.append(row);
    }
    if (!rows.length) {
      const empty = element('div', undefined, 'empty');
      empty.append(element('div','▦','empty-symbol'), element('h3','ไม่มีรายการจองในวันนี้'), element('p','No bookings in any room.'));
      list.append(empty);
    }
    wrap.append(list);
    const free = entries.filter(r => !r.error && !r.items.length).map(r => r.name);
    if (free.length) {
      const note = element('p', undefined, 'all-free');
      note.append(element('strong','ว่างทั้งวัน / Free all day: '), document.createTextNode(free.join(' · ')));
      wrap.append(note);
    }
    const broken = entries.filter(r => r.error).map(r => r.name);
    if (broken.length) {
      const note = element('p', undefined, 'all-free unavailable');
      note.append(element('strong','ไม่ทราบสถานะ / Unavailable: '), document.createTextNode(broken.join(' · ')));
      wrap.append(note);
    }
    return wrap;
  }
  function renderAllRooms() {
    const entries = allEntries();
    $('all-body').replaceChildren(allLayout === 'grid' ? buildGrid(entries) : buildList(entries));
    $('all-body').setAttribute('aria-busy','false');
    $('all-grid').setAttribute('aria-pressed', String(allLayout === 'grid'));
    $('all-list').setAttribute('aria-pressed', String(allLayout === 'list'));
    const now = new Date();
    const busyRooms = entries.filter(r => r.items.some(i => i.busy && i.startAt <= now && i.endAt > now)).length;
    const broken = entries.filter(r => r.error).length;
    $('event-count').textContent = `${entries.reduce((sum,r) => sum + r.items.length, 0)} รายการ / bookings`;
    if (failed || !navigator.onLine || Date.now() - lastFetch > 600000) unknown('ข้อมูลอาจไม่เป็นปัจจุบัน / Availability unknown');
    else if (broken) unknown(`อ่านไม่ได้ ${broken} ห้อง / ${broken} of ${entries.length} rooms unavailable`);
    else if (selectedDate !== dayString(now)) unknown('กำลังดูตารางวันที่เลือก / Selected day');
    else {
      $('availability').className = 'availability' + (busyRooms ? ' busy' : '');
      $('availability-text').textContent = `ใช้งานอยู่ ${busyRooms} ห้อง จาก ${entries.length} / ${busyRooms} of ${entries.length} rooms in use`;
    }
  }
  function setLayout(value) {
    if (allLayout === value) return;
    allLayout = value;
    try {localStorage.setItem('cp-all-layout',value);} catch (_) {}
    if (payload?.rooms) renderAllRooms();
  }
  function heading() {
    const isAll = viewMode === 'all';
    $('room-name').textContent = isAll ? 'ทุกห้อง / All rooms' : roomLabel(rooms[roomIndex]);
    $('room-select').value = rooms[roomIndex].key;
    $('room-count').textContent = rooms.length === 1 ? '1 ห้อง / 1 room' : `${roomIndex+1} / ${rooms.length}`;
    $('prev-room').disabled = $('next-room').disabled = rooms.length < 2 || isAll;
    $('room-dots').replaceChildren(...rooms.map((_,i) => element('i', undefined, i === roomIndex ? 'active' : '')));
    $('date').value = selectedDate;
    const isMonth = viewMode === 'month';
    $('viewer').classList.toggle('month-mode',isMonth);
    $('viewer').classList.toggle('all-mode',isAll);
    $('day-view').setAttribute('aria-pressed',String(viewMode === 'day'));
    $('month-view').setAttribute('aria-pressed',String(isMonth));
    $('all-view').setAttribute('aria-pressed',String(isAll));
    $('month-calendar').hidden = !isMonth;
    $('all-rooms').hidden = !isAll;
    $('schedule-label').replaceChildren(document.createTextNode(isMonth ? 'ปฏิทินรายเดือน' : isAll ? 'ทุกห้อง' : 'ตารางประจำวัน'), element('span',isMonth ? 'MONTHLY CALENDAR' : isAll ? 'ALL ROOMS' : 'DAILY SCHEDULE'));
    $('prev-day').setAttribute('aria-label',isMonth ? 'เดือนก่อนหน้า / Previous month' : 'วันก่อนหน้า / Previous day');
    $('next-day').setAttribute('aria-label',isMonth ? 'เดือนถัดไป / Next month' : 'วันถัดไป / Next day');
    $('prev-day').disabled = isMonth ? selectedDate.slice(0,7)==='2000-01' : selectedDate==='2000-01-01';
    $('next-day').disabled = isMonth ? selectedDate.slice(0,7)==='2100-12' : selectedDate==='2100-12-31';
    $('date-heading').textContent = isMonth ? new Intl.DateTimeFormat('th-TH',{month:'long',year:'numeric',timeZone:zone}).format(new Date(`${selectedDate}T12:00:00+07:00`)) : labelForDate(selectedDate);
  }
  async function load(clear = false) {
    if (controller) controller.abort();
    const active = new AbortController(); controller = active;
    loading = true; $('refresh').disabled = true; $('event-list').setAttribute('aria-busy','true');
    if (clear) { payload = null; failed = false; skeleton(); unknown('กำลังตรวจสอบ / Checking availability'); $('event-count').textContent = ''; $('updated').textContent = 'กำลังโหลด / Loading…'; }
    notify(''); $('reconnect').hidden = true;
    const timeout = setTimeout(() => active.abort(), 25000);
    let nextRefresh = viewMode === 'all' ? 300000 : 60000;
    try {
      const address = viewMode === 'all'
        ? `/api/day?${new URLSearchParams({date:selectedDate})}`
        : `/api/events?${new URLSearchParams({room:rooms[roomIndex].key,date:selectedDate,view:viewMode})}`;
      const response = await fetch(address, {signal:active.signal, credentials:'same-origin', cache:'no-store'});
      const data = await response.json();
      if (response.status === 401) $('reconnect').hidden = false;
      if (response.status === 429) nextRefresh = viewMode === 'all' ? 600000 : 120000;
      if (!response.ok) throw new Error(data.message || 'โหลดไม่สำเร็จ / Could not load calendar.');
      if (controller !== active) return;
      payload = data; failed = false; lastFetch = Date.now();
      if (data.room) updateRoomName(data.room.key,data.room.name);
      for (const room of data.rooms || []) if (!room.error) updateRoomName(room.key,room.name);
      $('updated').textContent = `อัปเดต ${timeString(new Date(data.fetched_at))} · รีเฟรชทุก ${viewMode === 'all' ? 5 : 1} นาที`;
      render();
    } catch (e) {
      if (controller !== active) return;
      failed = true;
      const text = e.name === 'AbortError' ? 'การเชื่อมต่อใช้เวลานาน กรุณาลองใหม่ / Connection timed out.' : e instanceof TypeError ? 'ไม่มีการเชื่อมต่อ / Unable to connect.' : e.message;
      notify(text);
      unknown('ตรวจสอบสถานะไม่ได้ / Availability unknown');
      if (!payload) { $('event-list').replaceChildren(); $('all-body').replaceChildren(); renderMonth([], false); } else render();
      $('updated').textContent = payload ? `ข้อมูลล่าสุด ${timeString(new Date(payload.fetched_at))} · อาจไม่เป็นปัจจุบัน` : 'ยังไม่มีข้อมูล / No current data';
    } finally {
      clearTimeout(timeout);
      if (controller === active) {
        loading = false; $('refresh').disabled = false; $('event-list').setAttribute('aria-busy','false'); $('month-days').setAttribute('aria-busy','false');
        clearTimeout(timer); if (!document.hidden) timer = setTimeout(() => load(), nextRefresh);
      }
    }
  }
  function changeRoom(delta) {
    if (rooms.length < 2) return;
    roomIndex = (roomIndex + delta + rooms.length) % rooms.length;
    try {localStorage.setItem('cp-room', rooms[roomIndex].key);} catch (_) {}
    heading(); load(true);
  }
  function changeDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value < '2000-01-01' || value > '2100-12-31') return;
    selectedDate = value; followsToday = value === dayString(new Date()); heading();
    if (viewMode === 'month' && payload?.view === 'month' && payload.month === value.slice(0,7)) render();
    else load(true);
  }
  function offsetDay(offset) {
    if(viewMode!=='month') {changeDate(shiftedDate(selectedDate,offset));return;}
    const d = new Date(`${selectedDate.slice(0,7)}-01T12:00:00+07:00`);
    d.setUTCMonth(d.getUTCMonth()+offset);
    const last = new Date(d); last.setUTCMonth(last.getUTCMonth()+1); last.setUTCDate(0);
    d.setUTCDate(Math.min(Number(selectedDate.slice(-2)),last.getUTCDate()));
    changeDate(dayString(d));
  }
  function switchView(value) {
    if (viewMode === value) return;
    viewMode = value; calendarSignature = '';
    try {localStorage.setItem('cp-view',value);} catch (_) {}
    heading(); load(true);
  }
  if (!rooms.length) { unknown('ยังไม่ได้เพิ่มห้อง / No rooms configured'); return; }
  rooms.forEach(r => { const o = element('option',roomLabel(r)); o.value=r.key; $('room-select').append(o); });
  $('room-select').addEventListener('change', e => changeRoom(rooms.findIndex(r => r.key===e.target.value)-roomIndex));
  $('day-view').addEventListener('click', () => switchView('day'));
  $('month-view').addEventListener('click', () => switchView('month'));
  $('all-view').addEventListener('click', () => switchView('all'));
  $('all-grid').addEventListener('click', () => setLayout('grid'));
  $('all-list').addEventListener('click', () => setLayout('list'));
  $('prev-room').addEventListener('click', () => changeRoom(-1));
  $('next-room').addEventListener('click', () => changeRoom(1));
  $('date').addEventListener('change', e => changeDate(e.target.value));
  $('prev-day').addEventListener('click', () => offsetDay(-1));
  $('next-day').addEventListener('click', () => offsetDay(1));
  $('today').addEventListener('click', () => changeDate(dayString(new Date())));
  $('refresh').addEventListener('click', () => load());
  const settingsPanel=$('settings-panel');
  $('open-settings').addEventListener('click',()=>settingsPanel.showModal());
  $('close-settings').addEventListener('click',()=>settingsPanel.close());
  settingsPanel.addEventListener('click',e=>{
    if(e.target!==settingsPanel) return;
    const bounds=settingsPanel.getBoundingClientRect();
    if(e.clientX<bounds.left || e.clientX>bounds.right || e.clientY<bounds.top || e.clientY>bounds.bottom) settingsPanel.close();
  });
  $('date-heading').addEventListener('click',()=>{
    $('date-controls').hidden=!$('date-controls').hidden;
    $('date-heading').setAttribute('aria-expanded',String(!$('date-controls').hidden));
    if(!$('date-controls').hidden) $('date').focus();
  });
  $('date').addEventListener('change',()=>{
    $('date-controls').hidden=true;
    $('date-heading').setAttribute('aria-expanded','false');
    $('date-heading').focus();
  });
  const zoomLevels = [100,140,180,220];
  let zoom = 0;
  try { const saved = Number(localStorage.getItem('cp-calendar-zoom')); if (Number.isInteger(saved) && saved>=0 && saved<zoomLevels.length) zoom=saved; } catch (_) {}
  function setZoom(value, center=false) {
    zoom = Math.max(0,Math.min(zoomLevels.length-1,value));
    $('calendar-canvas').dataset.zoom=String(zoom);
    queueCalendarFit();
    $('zoom-reset').textContent=`${zoomLevels[zoom]}%`;
    $('zoom-out').disabled=zoom===0;
    $('zoom-in').disabled=zoom===zoomLevels.length-1;
    try { localStorage.setItem('cp-calendar-zoom',String(zoom)); } catch (_) {}
    if (center) {
      const selected=$('month-days').querySelector('.is-selected');
      const scroller=$('calendar-scroll');
      if (selected) scroller.scrollLeft += selected.getBoundingClientRect().left-scroller.getBoundingClientRect().left-(scroller.clientWidth-selected.offsetWidth)/2;
    }
  }
  $('zoom-in').addEventListener('click',()=>setZoom(zoom+1,true));
  $('zoom-out').addEventListener('click',()=>setZoom(zoom-1,true));
  $('zoom-reset').addEventListener('click',()=>setZoom(0,true));
  setZoom(zoom);
  const textLevels = [50,60,70,85,100,120,140];
  let textSize = 4;
  try {
    const raw=localStorage.getItem('cp-calendar-text-percent');
    const legacy=localStorage.getItem('cp-calendar-text');
    const percent=raw!==null ? Number(raw) : legacy!==null ? [85,100,120,140][Number(legacy)] : 100;
    const saved=textLevels.indexOf(percent);
    if(saved>=0) textSize=saved;
  } catch (_) {}
  function setTextSize(value) {
    textSize=Math.max(0,Math.min(textLevels.length-1,value));
    $('calendar-canvas').dataset.text=String(textLevels[textSize]);
    queueCalendarFit();
    $('text-reset').textContent=`${textLevels[textSize]}%`;
    $('text-out').disabled=textSize===0;
    $('text-in').disabled=textSize===textLevels.length-1;
    try {localStorage.setItem('cp-calendar-text-percent',String(textLevels[textSize]));} catch (_) {}
  }
  $('text-out').addEventListener('click',()=>setTextSize(textSize-1));
  $('text-in').addEventListener('click',()=>setTextSize(textSize+1));
  $('text-reset').addEventListener('click',()=>setTextSize(4));
  setTextSize(textSize);
  let touch = null;
  $('viewer').addEventListener('touchstart', e => {
    touch=null;
    if (viewMode==='all' || e.touches.length!==1 || e.target.closest('input,select,button,a,.calendar-scroll,.grid-scroll')) return;
    touch = {x:e.changedTouches[0].clientX,y:e.changedTouches[0].clientY};
  }, {passive:true});
  $('viewer').addEventListener('touchcancel', () => {touch=null;}, {passive:true});
  $('viewer').addEventListener('touchend', e => {
    if (!touch) return;
    const dx=e.changedTouches[0].clientX-touch.x, dy=e.changedTouches[0].clientY-touch.y;
    if(Math.abs(dx)>80 && Math.abs(dx)>Math.abs(dy)*2) changeRoom(dx<0 ? 1 : -1);
    touch=null;
  }, {passive:true});
  if(document.fullscreenEnabled) {
    $('fullscreen').hidden=false;
    $('fullscreen').addEventListener('click', async () => {try {if(document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen();} catch (_) {notify('ใช้ Add to Home Screen เพื่อเปิดเต็มหน้าจอ / Add to Home Screen for a full-screen view.');}});
  }
  document.addEventListener('visibilitychange', () => {clearTimeout(timer); if(!document.hidden) {if(followsToday) selectedDate=dayString(new Date()); heading(); load(!payload || (viewMode==='month' ? payload.month!==selectedDate.slice(0,7) : payload.date!==selectedDate));}});
  window.addEventListener('online', () => load());
  window.addEventListener('offline', () => {failed=true; unknown('ไม่มีการเชื่อมต่อ / Offline'); notify('ข้อมูลอาจไม่เป็นปัจจุบัน / Displayed bookings may be outdated.');});
  window.addEventListener('pageshow', e => {if(e.persisted) location.reload();});
  setInterval(() => {
    if(document.hidden) return;
    if(followsToday && selectedDate!==dayString(new Date())) {changeDate(dayString(new Date())); return;}
    if(!loading) render();
  }, 15000);
  new ResizeObserver(queueCalendarFit).observe($('calendar-canvas'));
  document.fonts.ready.then(queueCalendarFit);
  heading(); skeleton(); load(true); loadRoomNames();
})();
