// Native offline OCR audit for rendered PDF pages. No network or model service.
// OCR success does not prove absence of logos or other identity without text.
import Foundation
import Vision
import ImageIO
import Darwin

struct PageInput: Decodable { let id: String; let path: String }
enum AuditError: Error { case invalid(String) }

func recognize(_ page: PageInput) -> [String: Any] {
    var row: [String: Any] = [
        "id": page.id, "path": page.path, "width": 0, "height": 0,
        "observations": [], "error": NSNull(), "text_detected": false,
        "backend": "Apple Vision VNRecognizeTextRequest", "recognition_level": "accurate",
        "recognition_languages": ["en-US"], "uses_language_correction": false,
        "uses_cpu_only": true, "bounding_box_format": "x,y,width,height",
        "bounding_box_origin": "lower_left", "image_orientation": "up",
    ]
    do {
        let url = URL(fileURLWithPath: page.path)
        guard url.pathExtension.lowercased() == "png",
              let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              CGImageSourceGetCount(source) == 1,
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw AuditError.invalid("Expected one readable PNG image")
        }
        row["width"] = image.width
        row["height"] = image.height
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
    return row
}

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: vision_ocr_audit manifest.json new-output.jsonl\n", stderr)
    exit(64)
}
do {
    let manifestURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let pages = try JSONDecoder().decode([PageInput].self, from: Data(contentsOf: manifestURL))
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
