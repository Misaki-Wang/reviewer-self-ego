// Native offline OCR audit for rendered PDF pages. No network or model service.
// OCR success does not prove absence of logos or other identity without text.
import Foundation
import Vision
import ImageIO
import Darwin
import CryptoKit

struct PageInput: Decodable { let id: String; let path: String; let sha256: String? }
func digest(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}
let executableURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL.resolvingSymlinksInPath()
let executableSHA256 = (try? Data(contentsOf: executableURL)).map(digest) ?? ""
var manifestSHA256 = ""
enum AuditError: Error { case invalid(String) }

func recognize(_ page: PageInput) -> [String: Any] {
    let started = Date().timeIntervalSinceReferenceDate
    var row: [String: Any] = [
        "id": page.id, "path": page.path, "width": 0, "height": 0,
        "schema_version": "offline-image-vision-ocr-2",
        "expected_png_sha256": page.sha256.map { $0.lowercased() } as Any? ?? NSNull(),
        "verified_png_sha256": NSNull(), "decoded_pixel_sha256": NSNull(),
        "manifest_sha256": manifestSHA256, "helper_executable_sha256": executableSHA256,
        "os_version": ProcessInfo.processInfo.operatingSystemVersionString,
        "observations": [], "error": NSNull(), "text_detected": false,
        "backend": "Apple Vision VNRecognizeTextRequest", "recognition_level": "accurate",
        "recognition_languages": ["en-US"], "uses_language_correction": false,
        "uses_cpu_only": true, "bounding_box_format": "x,y,width,height",
        "bounding_box_origin": "lower_left", "image_orientation": "up",
    ]
    do {
        let url = URL(fileURLWithPath: page.path)
        let raw = try Data(contentsOf: url)
        guard raw.count <= 256 * 1024 * 1024 else {
            throw AuditError.invalid("PNG exceeds the bounded input size")
        }
        let actual = digest(raw)
        row["verified_png_sha256"] = actual
        if let expected = page.sha256 {
            guard expected.range(of: "^[0-9a-fA-F]{64}$", options: .regularExpression) != nil,
                  expected.lowercased() == actual else {
                throw AuditError.invalid("PNG SHA256 mismatch or invalid expected hash")
            }
        }
        guard executableSHA256.count == 64, manifestSHA256.count == 64,
              url.pathExtension.lowercased() == "png",
              let source = CGImageSourceCreateWithData(raw as CFData, nil),
              CGImageSourceGetCount(source) == 1,
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw AuditError.invalid("Expected one readable PNG image")
        }
        guard image.width > 0, image.height > 0,
              image.width <= 32768, image.height <= 32768,
              image.width <= 32_000_000 / image.height,
              let pixels = image.dataProvider?.data else {
            throw AuditError.invalid("Unsupported image dimensions or pixel buffer")
        }
        row["width"] = image.width
        row["height"] = image.height
        row["decoded_pixel_sha256"] = digest(pixels as Data)
        row["bits_per_component"] = image.bitsPerComponent
        row["bits_per_pixel"] = image.bitsPerPixel
        row["bytes_per_row"] = image.bytesPerRow
        row["alpha_info"] = image.alphaInfo.rawValue
        row["color_space_name"] = image.colorSpace?.name.map { $0 as String } ?? "unknown"
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["en-US"]
        request.usesLanguageCorrection = false
        request.usesCPUOnly = true
        if #available(macOS 13.0, *) { request.automaticallyDetectsLanguage = false }
        row["request_revision"] = request.revision
        let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
        try handler.perform([request])
        let observations: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            return [
                "text": candidate.string, "confidence": Double(candidate.confidence),
                "bounding_box_normalized": [box.origin.x, box.origin.y, box.width, box.height],
            ]
        }
        row["observations"] = observations
        row["observation_count"] = observations.count
        row["text_detected"] = !observations.isEmpty
    } catch {
        row["error"] = String(describing: error)
        row["observation_count"] = 0
    }
    row["elapsed_seconds"] = Date().timeIntervalSinceReferenceDate - started
    return row
}

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: vision_ocr_audit manifest.json new-output.jsonl\n", stderr)
    exit(64)
}
do {
    let manifestURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let manifestData = try Data(contentsOf: manifestURL)
    manifestSHA256 = digest(manifestData)
    let pages = try JSONDecoder().decode([PageInput].self, from: manifestData)
    guard !pages.isEmpty, pages.allSatisfy({ !$0.id.isEmpty && !$0.path.isEmpty }),
          Set(pages.map(\.id)).count == pages.count else {
        throw AuditError.invalid("Manifest needs nonempty, unique IDs and nonempty paths")
    }
    let destination = CommandLine.arguments[2]
    let descriptor = open(destination, O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR)
    guard descriptor >= 0 else {
        throw AuditError.invalid("Cannot exclusively create output: \(String(cString: strerror(errno)))")
    }
    let output = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    defer { try? output.close() }
    var failures = 0
    for page in pages {
        let row = autoreleasepool { recognize(page) }
        if !(row["error"] is NSNull) { failures += 1 }
        var line = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
        line.append(0x0A)
        try output.write(contentsOf: line)
        try output.synchronize()
    }
    fputs("OCR completed: \(pages.count) pages, \(failures) failures.\n", stderr)
    if failures > 0 { exit(1) }
} catch {
    fputs("OCR audit failed: \(error)\n", stderr)
    exit(1)
}
