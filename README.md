# Verso

Edytor tekstu w stylu Edytora tekstu GNOME (GTK4, libadwaita, GtkSourceView), z podglądem:

- **Markdown** w stylu GitHuba: tabele, listy zadań, kolorowanie kodu, spis treści,
- **JSON** jako drzewo z rozwijaniem i wyszukiwaniem albo jako sformatowany tekst.

Tryby widoku: Edycja, Obok siebie, Podgląd (Ctrl+E przełącza). Pliki `.md` i `.json` otwierają się w podglądzie.

## Wymagania (Ubuntu 26.04)

```
sudo apt install gir1.2-webkit-6.0 gir1.2-gtksource-5 gir1.2-spelling-1
```

Python, GTK4, libadwaita i `python3-markdown` są w systemie.

## Uruchomienie i instalacja

```
./bin/verso plik.md          # z repozytorium
./install.sh                   # skrót w menu, ikona, domyślny dla .md i .json (bez sudo)
./install.sh --no-default      # bez zmiany domyślnych programów
./uninstall.sh                 # usuwa i przywraca Edytor tekstu
```

## Testy

```
guardrun -- python3 -m pytest tests -q
```

`VERSO_SCREENSHOT=out.png ./bin/verso plik.md` zapisuje zrzut okna i kończy (opcjonalnie `VERSO_MODE=split`, `VERSO_DARK=1`).
