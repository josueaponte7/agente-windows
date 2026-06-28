// App Flutter — ordenes (texto/voz), respuesta con Markdown + voz, y VER CAMARA
//
// Requisitos:
//   flutter pub add http
//   flutter pub add speech_to_text
//   flutter pub add flutter_tts
//   flutter pub add gpt_markdown
//   En android/app/src/main/AndroidManifest.xml, dentro de <manifest>:
//     <uses-permission android:name="android.permission.RECORD_AUDIO"/>

import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:speech_to_text/speech_to_text.dart' as stt;
import 'package:flutter_tts/flutter_tts.dart';
import 'package:gpt_markdown/gpt_markdown.dart';

void main() => runApp(const AgenteApp());

String limpiarMarkdown(String texto) {
  return texto
      .replaceAll(RegExp(r'\*\*'), '')
      .replaceAll(RegExp(r'\*'), '')
      .replaceAll(RegExp(r'`+'), '')
      .replaceAll(RegExp(r'^#+\s*', multiLine: true), '')
      .replaceAll(RegExp(r'(?<!\w)_(?!\w)'), '')
      .trim();
}

class AgenteApp extends StatelessWidget {
  const AgenteApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Agente',
      theme: ThemeData.dark(useMaterial3: true),
      home: const PantallaAgente(),
    );
  }
}

class PantallaAgente extends StatefulWidget {
  const PantallaAgente({super.key});

  @override
  State<PantallaAgente> createState() => _PantallaAgenteState();
}

class _PantallaAgenteState extends State<PantallaAgente> {
  final _ip = TextEditingController(text: '192.168.86.45');
  final _token = TextEditingController(text: 'miclave123');
  final _orden = TextEditingController();

  String _respuesta = '';
  bool _cargando = false;

  final stt.SpeechToText _voz = stt.SpeechToText();
  bool _escuchando = false;

  final FlutterTts _tts = FlutterTts();

  Uint8List? _foto;       // ultima foto recibida de la camara del portatil
  bool _cargandoFoto = false;

  @override
  void initState() {
    super.initState();
    _tts.setLanguage('es-ES');
    _tts.setSpeechRate(0.5);
  }

  Future<void> _leer(String texto) async {
    final limpio = limpiarMarkdown(texto);
    if (limpio.isEmpty) return;
    await _tts.stop();
    await _tts.speak(limpio);
  }

  Future<void> _toggleVoz() async {
    if (_escuchando) {
      await _voz.stop();
      setState(() => _escuchando = false);
      return;
    }
    final disponible = await _voz.initialize(
      onStatus: (s) {
        if (s == 'done' || s == 'notListening') {
          setState(() => _escuchando = false);
        }
      },
      onError: (e) => setState(() => _escuchando = false),
    );
    if (!disponible) {
      setState(() => _respuesta = 'No se pudo iniciar el reconocimiento de voz.');
      return;
    }
    setState(() => _escuchando = true);
    _voz.listen(
      localeId: 'es_ES',
      onResult: (r) {
        setState(() => _orden.text = r.recognizedWords);
      },
    );
  }

  Future<void> _enviar() async {
    final orden = _orden.text.trim();
    if (orden.isEmpty) return;

    setState(() {
      _cargando = true;
      _respuesta = 'Enviando...';
    });

    try {
      final url = Uri.parse('http://${_ip.text.trim()}:5000/orden');
      final r = await http
          .post(
            url,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'token': _token.text.trim(), 'orden': orden}),
          )
          .timeout(const Duration(seconds: 60));

      final data = jsonDecode(utf8.decode(r.bodyBytes));
      final texto = data['respuesta'] ?? data['error'] ?? '(sin respuesta)';
      setState(() {
        _respuesta = texto;
        _orden.clear();
      });
      _leer(texto);
    } catch (e) {
      setState(() => _respuesta = 'Error de conexion: $e');
    } finally {
      setState(() => _cargando = false);
    }
  }

  // Pide la foto al portatil y la muestra.
  Future<void> _verCamara() async {
    setState(() => _cargandoFoto = true);
    try {
      final url = Uri.parse(
          'http://${_ip.text.trim()}:5000/camara?token=${Uri.encodeComponent(_token.text.trim())}');
      final r = await http.get(url).timeout(const Duration(seconds: 30));
      if (r.statusCode == 200) {
        setState(() => _foto = r.bodyBytes);
      } else {
        setState(() => _respuesta = 'No se pudo obtener la foto (${r.statusCode}).');
      }
    } catch (e) {
      setState(() => _respuesta = 'Error al pedir la foto: $e');
    } finally {
      setState(() => _cargandoFoto = false);
    }
  }

  // Pide foto + descripcion: muestra la imagen Y dice/lee que ve.
  Future<void> _verCamaraDescribe() async {
    setState(() {
      _cargandoFoto = true;
      _respuesta = 'Mirando...';
    });
    try {
      final url = Uri.parse('http://${_ip.text.trim()}:5000/camara_describe');
      final r = await http
          .post(
            url,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'token': _token.text.trim(), 'orden': ''}),
          )
          .timeout(const Duration(seconds: 60));
      final data = jsonDecode(utf8.decode(r.bodyBytes));
      if (data['imagen'] != null) {
        setState(() {
          _foto = base64Decode(data['imagen']);
          _respuesta = data['descripcion'] ?? '';
        });
        _leer(_respuesta);
      } else {
        setState(() => _respuesta = data['error'] ?? 'No se pudo describir.');
      }
    } catch (e) {
      setState(() => _respuesta = 'Error: $e');
    } finally {
      setState(() => _cargandoFoto = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Agente')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: ListView(
          children: [
            TextField(
              controller: _ip,
              decoration: const InputDecoration(
                labelText: 'IP del portatil',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _token,
              obscureText: true,
              decoration: const InputDecoration(
                labelText: 'Token',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _orden,
              decoration: InputDecoration(
                labelText: 'Orden',
                hintText: 'escribe o pulsa el microfono',
                border: const OutlineInputBorder(),
                suffixIcon: IconButton(
                  icon: Icon(_escuchando ? Icons.mic : Icons.mic_none,
                      color: _escuchando ? Colors.redAccent : null),
                  onPressed: _toggleVoz,
                ),
              ),
              onSubmitted: (_) => _enviar(),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: FilledButton(
                    onPressed: _cargando ? null : _enviar,
                    child: Text(_cargando ? 'Esperando...' : 'Enviar'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: FilledButton.tonalIcon(
                    onPressed: _cargandoFoto ? null : _verCamara,
                    icon: const Icon(Icons.photo_camera),
                    label: const Text('Ver camara'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton.tonalIcon(
                    onPressed: _cargandoFoto ? null : _verCamaraDescribe,
                    icon: const Icon(Icons.visibility),
                    label: const Text('Ver + describir'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            // La foto de la camara del portatil (si se ha pedido)
            if (_foto != null)
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.memory(_foto!),
              ),
            if (_foto != null) const SizedBox(height: 20),
            // La respuesta del agente
            if (_respuesta.isNotEmpty)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white10,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    GptMarkdown(_respuesta),
                    if (!_respuesta.startsWith('Enviando') &&
                        !_respuesta.startsWith('Error'))
                      Align(
                        alignment: Alignment.centerRight,
                        child: IconButton(
                          icon: const Icon(Icons.volume_up),
                          onPressed: () => _leer(_respuesta),
                        ),
                      ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}
