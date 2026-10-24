# ADR 0002 — Plugin manifest, namespace, host policy và catalog version

- Trạng thái: Accepted.
- Ngày: 2026-10-18.
- Phạm vi: Ngày 80; tiếp nối [ADR 0001](0001-v2-contracts-and-layer-ownership.md).

## Quyết định

1. Manifest ứng dụng có schema version 1.0, exact SemVer và typed declarations cho MCP/Skill.
   Chỉ mang metadata, host policy và logical profile/credential slots; không chứa executable/args,
   endpoint, raw credential hoặc instructions. Backend sở hữu profile/credential/grant resolution.
2. Tách canonical identity, alias runtime và remote MCP name. Host cấp namespace; canonical ID có
   segment loại `tool`/`skill`; alias ba component không quá 64 ký tự và không normalize mơ hồ.
3. Policy mặc định admin/approval/unknown/no retry. Manifest là host-reviewed config, MCP annotations
   không được hạ policy. Skill dependency không được nới policy của tool. Enforcement vẫn ở executor.
4. Catalog trong memory publish snapshot bất biến theo một thao tác sau khi validate. Version gồm
   revision và hash canonical; mutation hỗ trợ CAS. Disable/enable tăng revision để tránh ABA.
5. Published manifest/policy version không sửa nội dung trong cùng instance catalog; JSON snapshot
   có checksum và nested revalidation. Hash xác định nội dung, không chứng minh publisher trusted.
6. Ngày 80 chỉ cung cấp model/catalog/validator/examples/schema/tests. Không nối vào graph, không đổi
   registry/alias V1, không tạo DB/API hoặc transport runtime mới. Các flag V2 vẫn tắt.

## Hệ quả

- Đổi description cần manifest version mới; đổi policy, binding hoặc release pin cần thêm policy version.
- Namespace ràng buộc với plugin trong active catalog; collision fail trước publish. Rename active
  namespace bị từ chối; migration identity cần thiết kế riêng.
- Version hash và dữ liệu canonical đủ để audit/cache pin metadata; authorization/version check thực
  thi, event invalidation và distributed persistence vẫn phải bổ sung ở các ngày sau.
- Backend Ngày 81 lưu authoritative catalog/connection/grant và history; Ngày 82–83 resolve profile,
  start/stop/health, negotiate protocol và SSRF/auth. Ngày 84 discovery lọc quyền trước shortlist;
  Ngày 85 kiểm tra filesystem/package/Skill compatibility khi load thực tế.

Field và ví dụ: [Plugin guide](../plugins/README.md).
