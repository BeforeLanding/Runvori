package dev.runvori.app.domain

/**
 * Minimal JSON reader/writer for the v1 protocol.
 *
 * The client needs two things the platform JSON APIs do not give directly: a
 * strict parse that rejects anything unexpected, and a writer whose output is
 * stable for request fingerprints. Maps become insertion-ordered objects.
 */
object JsonValue {
    fun parse(raw: String): Any? {
        val reader = Reader(raw)
        val value = reader.readValue()
        reader.skipWhitespace()
        if (!reader.atEnd()) throw JsonFormat("trailing_content")
        return value
    }

    fun stringify(value: Any?): String {
        val builder = StringBuilder()
        write(builder, value)
        return builder.toString()
    }

    private fun write(builder: StringBuilder, value: Any?) {
        when (value) {
            null -> builder.append("null")
            is String -> writeString(builder, value)
            is Boolean -> builder.append(if (value) "true" else "false")
            is Int, is Long -> builder.append(value.toString())
            is Double -> {
                if (!value.isFinite()) throw JsonFormat("non_finite_number")
                builder.append(value.toString())
            }
            is Map<*, *> -> {
                builder.append('{')
                var first = true
                for ((key, item) in value) {
                    if (key !is String) throw JsonFormat("non_string_key")
                    if (!first) builder.append(',')
                    first = false
                    writeString(builder, key)
                    builder.append(':')
                    write(builder, item)
                }
                builder.append('}')
            }
            is Iterable<*> -> {
                builder.append('[')
                var first = true
                for (item in value) {
                    if (!first) builder.append(',')
                    first = false
                    write(builder, item)
                }
                builder.append(']')
            }
            else -> throw JsonFormat("unsupported_value")
        }
    }

    private fun writeString(builder: StringBuilder, value: String) {
        builder.append('"')
        for (character in value) {
            when (character) {
                '"' -> builder.append("\\\"")
                '\\' -> builder.append("\\\\")
                '\n' -> builder.append("\\n")
                '\r' -> builder.append("\\r")
                '\t' -> builder.append("\\t")
                '\b' -> builder.append("\\b")
                else -> if (character < ' ') {
                    builder.append("\\u")
                    builder.append(character.code.toString(16).padStart(4, '0'))
                } else {
                    builder.append(character)
                }
            }
        }
        builder.append('"')
    }

    private class Reader(private val text: String) {
        private var index = 0

        fun atEnd(): Boolean = index >= text.length

        fun skipWhitespace() {
            while (index < text.length && text[index].isWhitespace()) index++
        }

        fun readValue(): Any? {
            skipWhitespace()
            if (atEnd()) throw JsonFormat("unexpected_end")
            return when (val character = text[index]) {
                '{' -> readObject()
                '[' -> readArray()
                '"' -> readString()
                't' -> readLiteral("true", true)
                'f' -> readLiteral("false", false)
                'n' -> readLiteral("null", null)
                else -> if (character == '-' || character.isDigit()) {
                    readNumber()
                } else {
                    throw JsonFormat("unexpected_character")
                }
            }
        }

        private fun readObject(): Map<String, Any?> {
            index++ // {
            val result = LinkedHashMap<String, Any?>()
            skipWhitespace()
            if (peek() == '}') { index++; return result }
            while (true) {
                skipWhitespace()
                if (peek() != '"') throw JsonFormat("expected_key")
                val key = readString()
                skipWhitespace()
                if (peek() != ':') throw JsonFormat("expected_colon")
                index++
                if (result.containsKey(key)) throw JsonFormat("duplicate_key")
                result[key] = readValue()
                skipWhitespace()
                when (peek()) {
                    ',' -> index++
                    '}' -> { index++; return result }
                    else -> throw JsonFormat("expected_object_end")
                }
            }
        }

        private fun readArray(): List<Any?> {
            index++ // [
            val result = ArrayList<Any?>()
            skipWhitespace()
            if (peek() == ']') { index++; return result }
            while (true) {
                result.add(readValue())
                skipWhitespace()
                when (peek()) {
                    ',' -> index++
                    ']' -> { index++; return result }
                    else -> throw JsonFormat("expected_array_end")
                }
            }
        }

        private fun readString(): String {
            index++ // opening quote
            val builder = StringBuilder()
            while (true) {
                if (atEnd()) throw JsonFormat("unterminated_string")
                when (val character = text[index]) {
                    '"' -> { index++; return builder.toString() }
                    '\\' -> {
                        index++
                        if (atEnd()) throw JsonFormat("unterminated_escape")
                        when (val escape = text[index]) {
                            '"' -> builder.append('"')
                            '\\' -> builder.append('\\')
                            '/' -> builder.append('/')
                            'b' -> builder.append('\b')
                            'f' -> builder.append(12.toChar())
                            'n' -> builder.append('\n')
                            'r' -> builder.append('\r')
                            't' -> builder.append('\t')
                            'u' -> {
                                if (index + 4 >= text.length) {
                                    throw JsonFormat("bad_unicode_escape")
                                }
                                val hex = text.substring(index + 1, index + 5)
                                val code = hex.toIntOrNull(16)
                                    ?: throw JsonFormat("bad_unicode_escape")
                                builder.append(code.toChar())
                                index += 4
                            }
                            else -> throw JsonFormat("bad_escape")
                        }
                        index++
                    }
                    else -> {
                        if (character < ' ') throw JsonFormat("control_in_string")
                        builder.append(character)
                        index++
                    }
                }
            }
        }

        private fun readLiteral(token: String, value: Any?): Any? {
            if (!text.startsWith(token, index)) throw JsonFormat("bad_literal")
            index += token.length
            return value
        }

        private fun readNumber(): Any {
            val start = index
            if (peek() == '-') index++
            while (!atEnd() && text[index].isDigit()) index++
            var isDouble = false
            if (!atEnd() && text[index] == '.') {
                isDouble = true
                index++
                while (!atEnd() && text[index].isDigit()) index++
            }
            if (!atEnd() && (text[index] == 'e' || text[index] == 'E')) {
                isDouble = true
                index++
                if (!atEnd() && (text[index] == '+' || text[index] == '-')) index++
                while (!atEnd() && text[index].isDigit()) index++
            }
            if (index == start) throw JsonFormat("bad_number")
            val raw = text.substring(start, index)
            return if (isDouble) raw.toDoubleOrNull()
                ?: throw JsonFormat("bad_number")
            else raw.toLongOrNull() ?: throw JsonFormat("bad_number")
        }

        private fun peek(): Char {
            if (atEnd()) throw JsonFormat("unexpected_end")
            return text[index]
        }
    }
}

class JsonFormat(val code: String) : Exception(code)