import AppKit

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let context = NSGraphicsContext.current!.cgContext
        context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
        let frame = NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 218, yRadius: 218)
        NSGradient(starting: NSColor(red: 0.10, green: 0.20, blue: 0.28, alpha: 1),
                   ending: NSColor(red: 0.08, green: 0.48, blue: 0.48, alpha: 1))!.draw(in: frame, angle: 45)
        let shackle = NSBezierPath()
        shackle.move(to: NSPoint(x: 348, y: 538))
        shackle.line(to: NSPoint(x: 348, y: 665))
        shackle.curve(to: NSPoint(x: 664, y: 715), controlPoint1: NSPoint(x: 348, y: 868), controlPoint2: NSPoint(x: 664, y: 868))
        shackle.lineWidth = 64
        shackle.lineCapStyle = .round
        NSColor.white.setStroke()
        shackle.stroke()
        NSColor.white.setFill()
        NSBezierPath(roundedRect: NSRect(x: 274, y: 250, width: 476, height: 326), xRadius: 70, yRadius: 70).fill()
        NSColor(red: 0.08, green: 0.32, blue: 0.37, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 474, y: 410, width: 76, height: 76)).fill()
        NSBezierPath(roundedRect: NSRect(x: 489, y: 340, width: 46, height: 100), xRadius: 15, yRadius: 15).fill()
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: destination.appendingPathComponent(name))
    }
}
