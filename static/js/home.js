// === 로비 WebSocket: 방 삭제/생성/수정 실시간 반영 (이 블록만 유지!) ===
(() => {
  const list = document.getElementById("room-list");
  if (!list) return;

  // 1) WebSocket URL 구성
  const scheme = (location.protocol === "https:") ? "wss" : "ws";
  const wsUrl = `${scheme}://${location.host}/ws/lobby/`;

  // 2) 도우미: li 찾기 / 만들기
  function findItemBySlug(slug) {
    return list.querySelector(`li[data-room-slug="${slug}"]`);
  }
  function removeRoomBySlug(slug) {
    const li = findItemBySlug(slug);
    if (li) li.remove();
  }

  // 3) 메타 문자열을 안전하게 만들기
  function buildMeta({ topic, created_at, owner, locked }) {
    const bits = [];
    bits.push(`주제: ${topic || '-'}`);
    if (created_at) bits.push(`생성: ${created_at}`);
    if (owner) bits.push(`방장: ${owner}`);
    if (locked) bits.push('🔒');
    return bits.join(' · ');
  }

  // 4) 리스트 카드 생성/갱신(Upsert)
  function upsertRoomCard({ id, slug, name, topic, owner, created_at, locked }) {
    // li 찾기: data-room-slug가 주 식별자
    let li = findItemBySlug(slug);

    if (!li) {
      // 새 카드 생성 (템플릿과 클래스 맞춤)
      li = document.createElement("li");
      li.className = "item";
      // 템플릿은 id="room-{{ r.id }}"이지만 이벤트에서 id가 없을 수도 있어서
      // id가 있으면 room-<id>, 없으면 room-<slug>로 부여
      li.id = id ? `room-${id}` : `room-${slug}`;
      li.setAttribute("data-room-slug", slug);

      li.innerHTML = `
        <div class="item__left">
          <div class="item__thumb">🏠</div>
          <div>
            <div class="item__title font-medium">${name || slug}</div>
            <div class="item__meta">${buildMeta({ topic, created_at, owner, locked })}</div>
          </div>
        </div>
        <a href="/rooms/${slug}/"
           class="link enter-btn"
           data-slug="${slug}"
           ${locked ? 'data-locked="1"' : ''}>입장</a>
      `;
      list.prepend(li);
      return;
    }

    // 기존 카드 갱신
    const titleEl = li.querySelector(".item__title");
    const metaEl  = li.querySelector(".item__meta");
    const enter   = li.querySelector("a.enter-btn");

    if (titleEl && name) titleEl.textContent = name;
    if (metaEl) {
      // topic/owner/🔒 등 변경 분 반영
      metaEl.textContent = buildMeta({
        topic: (topic ?? extractTopic(metaEl.textContent)),
        created_at: extractCreatedAt(metaEl.textContent, created_at),
        owner: (owner ?? extractOwner(metaEl.textContent)),
        locked: (typeof locked === 'boolean') ? locked : metaEl.textContent.includes('🔒'),
      });
    }
    if (enter) {
      // 잠금 상태 변경 시 버튼 data-locked 토글
      if (typeof locked === 'boolean') {
        if (locked) enter.setAttribute('data-locked', '1');
        else enter.removeAttribute('data-locked');
      }
      // slug가 바뀌지 않는 전제(일반적으로 방 slug는 불변)
      enter.setAttribute('href', `/rooms/${slug}/`);
      enter.setAttribute('data-slug', slug);
    }
  }

  // 5) 기존 메타에서 토막 추출(없으면 기존 유지 목적)
  function extractTopic(s) {
    const m = s.match(/주제:\s*([^·]+)/); return m ? m[1].trim() : undefined;
  }
  function extractOwner(s) {
    const m = s.match(/방장:\s*([^·]+)/); return m ? m[1].trim() : undefined;
  }
  function extractCreatedAt(s, incoming) {
    if (incoming) return incoming;
    const m = s.match(/생성:\s*([^·]+)/);
    return m ? m[1].trim() : undefined;
  }

  // 6) 수신 이벤트 스위치
  function handleLobbyEvent(data) {
    // 서버에서 보낼 수 있는 키를 널널하게 지원
    const ev   = data.event;                      // "room_updated" | "room_closed" | "room_created"
    const slug = data.slug || data.room_slug;     // slug 별칭
    const id   = data.id   || data.room_id;       // id 별칭

    if (!ev) return;

    if (ev === "room_closed" || ev === "room_deleted") {
      if (slug) removeRoomBySlug(slug);
      // (선택) 사용자에게 알림
      if (typeof window.showToast === "function") {
        showToast("warning", "방이 삭제되어 목록에서 제거되었습니다.");
      }
      return;
    }

    if (ev === "room_created") {
      upsertRoomCard({
        id, slug,
        name:   data.name   || data.room_name || slug,
        topic:  data.topic,
        owner:  data.owner  || data.owner_name,
        created_at: data.created_at || "방금",
        locked: !!data.locked,
      });
      return;
    }

    if (ev === "room_updated") {
      upsertRoomCard({
        id, slug,
        name:   data.name   || data.room_name,
        topic:  data.topic,
        owner:  data.owner  || data.owner_name,
        locked: (typeof data.locked === 'boolean') ? data.locked : undefined,
      });
      return;
    }
  }

  // 7) 소켓 연결/재연결
  let s;
  function connect() {
    s = new WebSocket(wsUrl);
    s.onopen = () => console.log("✅ 로비 WebSocket 연결됨");
    s.onerror = (err) => console.error("❌ 로비 WebSocket 에러", err);
    s.onclose = () => setTimeout(connect, 1000);   // 1초 후 재연결
    s.onmessage = (e) => {
      let data; try { data = JSON.parse(e.data); } catch { return; }
      console.log("로비 메시지 수신:", data);
      handleLobbyEvent(data);
    };
  }
  connect();
})();
