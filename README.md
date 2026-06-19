# BBox Label Editor

Pascal VOC XML 어노테이션의 바운딩박스와 라벨을 시각적으로 검수하고 수정할 수 있는 PyQt5 데스크톱 도구입니다.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![PyQt5](https://img.shields.io/badge/PyQt5-5.15+-green)

## Features

- 이미지 위에 바운딩박스 + 라벨 오버레이 표시
- 박스 클릭으로 선택 후 라벨 직접 수정
- XML 없는 이미지는 "작업 불가" 배너 표시
- 이미지/XML 혼합 폴더 및 분리 폴더 모두 지원
- 수정 전 자동 백업 (.bak)
- Undo 지원 (Ctrl+Z)

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
python bbox_label_editor.py
```

1. **이미지 폴더** 선택 (하위폴더 자동 재귀 탐색)
2. XML이 별도 폴더에 있으면 **XML 폴더** 추가 선택
3. **불러오기** 클릭
4. 왼쪽 목록에서 이미지 선택 -> 바운딩박스 오버레이 확인
5. 박스 클릭 -> 라벨 수정 -> Enter
6. **Ctrl+S** 로 저장

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+S` | 저장 |
| `Ctrl+Z` | 되돌리기 |
| `A` / `←` | 이전 이미지 |
| `D` / `→` | 다음 이미지 |
| `Tab` | 다음 박스 |
| `Shift+Tab` | 이전 박스 |
| `Enter` | 라벨 적용 |
| `Escape` | 박스 선택 해제 |

## Supported Format

Pascal VOC XML:

```xml
<annotation>
  <object>
    <name>K</name>
    <bndbox>
      <xmin>136</xmin>
      <ymin>107</ymin>
      <xmax>162</xmax>
      <ymax>147</ymax>
    </bndbox>
  </object>
</annotation>
```
