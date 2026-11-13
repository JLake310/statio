package com.statio.app

import java.net.URLEncoder

/**
 * 휴대폰으로 넘길 주차 위치 링크입니다. QR에 담깁니다.
 * 카카오맵 링크라서 휴대폰 카메라로 찍으면 지도에 주차 위치가 '내 차 · 주차장 · 층' 이름으로 찍힙니다.
 * Statio 서버가 따로 없어서 링크에는 좌표와 이름만 들어갑니다.
 */
object PhoneCard {

    fun url(record: ParkingRecord): String {
        val label = listOfNotNull("내 차", record.place, record.floor).joinToString(" ").replace(",", " ")
        return "https://map.kakao.com/link/map/${enc(label)},${record.lat},${record.lon}"
    }

    private fun enc(text: String): String = URLEncoder.encode(text, "UTF-8").replace("+", "%20")
}
